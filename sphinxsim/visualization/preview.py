"""Pre-run simulation configuration visualizer.

Renders an interactive 3-D (or 2-D) preview of the simulation setup —
geometries, boundary conditions and body annotations — from a validated
:class:`~sphinxsim.config.schemas.SimulationConfig`.

Two rendering modes are supported, tried in order:

VTP mode (preferred)
    The C++ ``buildGeometries()`` stage is invoked and the resulting
    ``Shape<Name>.vtp`` polygon meshes are loaded and displayed by PyVista.
    The lightweight C++ ``GeometryBuilder`` is used for this stage.

C++ bounds cache
    When VTP files are not produced, accurate bounding boxes are queried
    directly from the geometry builder via ``getShapeBounds()`` and reused
    for preview rendering.

The C++ extension (``_sphinxsys_core_2d`` or ``_sphinxsys_core_3d``) must
be installed.  If it is not found an :class:`ImportError` is raised with
a clear install hint.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sphinxsim.bindings.loader import load_sphinxsys_core_nd

if TYPE_CHECKING:
    from sphinxsim.config.schemas import (
        BodyConstraintConfig,
        OrientedBoxConfig,
        ShapeConfig,
        SimulationConfig,
    )


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

# Colours assigned per body category so users instantly see what is what.
_FLUID_COLOUR = (0.20, 0.53, 0.85)       # blue
_SOLID_COLOUR = (0.70, 0.70, 0.70)       # grey
_CONTINUUM_COLOUR = (0.90, 0.60, 0.10)   # amber
_UNKNOWN_COLOUR = (0.60, 0.80, 0.40)     # green (shapes not in any body list)
_INLET_OUTLET_COLOUR = (0.85, 0.20, 0.20)  # red
_REGION_COLOUR = (0.85, 0.70, 0.10)        # yellow
_OBSERVER_COLOUR = (0.93, 0.13, 0.93)      # magenta — observer positions
_CONSTRAINT_COLOUR = (0.93, 0.55, 0.13)     # orange — body constraint regions
_GRAVITY_COLOUR = (0.10, 0.90, 0.90)        # cyan — gravity direction arrow
_PARTICLE_POINT_SIZE = 5


def _body_colour(body_name: str, config: "SimulationConfig") -> tuple[float, float, float]:
    for b in config.fluid_bodies:
        if b.name == body_name:
            return _FLUID_COLOUR
    for b in config.solid_bodies:
        if b.name == body_name:
            return _SOLID_COLOUR
    for b in config.continuum_bodies:
        if b.name == body_name:
            return _CONTINUUM_COLOUR
    return _UNKNOWN_COLOUR


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _bounds_to_box(lower: list[float], upper: list[float]) -> Any:
    """Create a PyVista box mesh from lower/upper bounds."""
    import pyvista as pv  # type: ignore[import]

    if len(lower) == 2:
        # 2-D: extrude a thin slab so the box is still visible in 3-D view
        return pv.Box(
            bounds=(lower[0], upper[0], lower[1], upper[1], -0.01, 0.01)
        )
    return pv.Box(bounds=(lower[0], upper[0], lower[1], upper[1], lower[2], upper[2]))


def _label_anchor_point(mesh: Any) -> tuple[float, float, float]:
    """Choose a label position inside *mesh* when possible.

    For concave shapes the geometric center can lie outside. We sample a few
    points inside the axis-aligned bounds and keep the first point confirmed as
    enclosed by the surface. If enclosure checks are unavailable, we fall back
    to the mesh center.
    """
    try:
        import pyvista as pv  # type: ignore[import]
    except Exception:
        return tuple(float(v) for v in mesh.center)

    bounds = mesh.bounds
    x0, x1, y0, y1, z0, z1 = (float(v) for v in bounds)
    center = tuple(float(v) for v in mesh.center)

    # Probe from center outward; using interior fractions avoids boundary points.
    fractions = (0.5, 0.35, 0.65, 0.2, 0.8)
    candidates = []
    for fx in fractions:
        x = x0 + (x1 - x0) * fx
        for fy in fractions:
            y = y0 + (y1 - y0) * fy
            for fz in fractions:
                z = z0 + (z1 - z0) * fz
                candidates.append((x, y, z))

    # Ensure the geometric center is always tested first.
    candidates.insert(0, center)

    try:
        points = pv.PolyData(candidates)
        selected = points.select_interior_points(
            mesh,
            tolerance=1e-6,
            check_surface=False,
        )
        mask = selected["SelectedPoints"]
        for idx, value in enumerate(mask):
            if int(value) == 1:
                point = candidates[idx]
                return (float(point[0]), float(point[1]), float(point[2]))
    except Exception:
        pass

    return center


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ConfigVisualizer:
    """Visualize a simulation configuration before running the solver.

    Parameters
    ----------
    config:
        Validated :class:`~sphinxsim.config.schemas.SimulationConfig`.
    project_root:
        Root of the SPHinXsim project (used to locate temporary build files).
    config_path:
        Path to the original JSON config file.  Passed directly to
        :class:`SPHSimulation` — the file is the single source of truth.
        Required for C++ geometry building; if omitted no shapes are rendered.
    off_screen:
        When *True*, render to an off-screen buffer instead of opening a
        window.  Useful for testing.
    """

    def __init__(
        self,
        config: "SimulationConfig",
        project_root: Path,
        *,
        config_path: Path | None = None,
        off_screen: bool = False,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.config_path = Path(config_path) if config_path is not None else None
        self.off_screen = off_screen

        self._vtp_dir: Path | None = None
        self._shape_bounds_cache: dict[str, Any] | None = None
        self._annotation_label_actors: list[dict[str, Any]] = []

    def _spatial_dim(self) -> int:
        """Return the spatial dimension (2 or 3) inferred from the config.

        Checks multiple vector fields in order of reliability so the correct
        binding module is selected even when ``system_domain`` is absent.
        """
        geo = self.config.geometries

        # Most reliable: system_domain explicitly declares the bounding box.
        if geo.system_domain is not None:
            return len(geo.system_domain.lower_bound)

        # Top-level gravity vector.
        if self.config.gravity is not None:
            return len(self.config.gravity)

        # Simbody planar constraints are an explicit 2-D signal.
        for constraint in self.config.body_constraints:
            if constraint.type.value == "simbody":
                if (constraint.mobilized_body or "").lower() == "planar":
                    return 2
                if constraint.velocity is not None:
                    return len(constraint.velocity)

        # Walk shapes: bounding_box / box / expanded_box carry explicit vectors.
        for shape in geo.shapes:
            for vec in (shape.lower_bound, shape.upper_bound, shape.half_size):
                if vec is not None:
                    return len(vec)
            if shape.transform is not None:
                return len(shape.transform.translation)
            # triangle_mesh: translation field is 3-D only (min_length=3).
            if shape.translation is not None:
                return len(shape.translation)

        # Walk oriented boxes: center / normal / half_size.
        for ob in geo.oriented_boxes:
            for vec in (ob.center, ob.normal, ob.half_size):
                if vec is not None:
                    return len(vec)
            if ob.transform is not None:
                return len(ob.transform.translation)

        return 3  # safe default — 3-D module handles most cases

    @property
    def used_cpp_geometry(self) -> bool:
        """Whether the most recent preview used C++-generated VTP geometry."""
        return self._vtp_dir is not None

    @property
    def used_cpp_bounds(self) -> bool:
        """Whether the most recent preview cached C++ shape bounds."""
        return self._shape_bounds_cache is not None

    @property
    def annotation_label_actors(self) -> list[dict[str, Any]]:
        """Label actors created by the latest preview population pass."""
        return list(self._annotation_label_actors)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview(
        self,
        *,
        title: str = "SPHinXsim - Configuration Preview",
        screenshot_path: str | Path | None = None,
        with_particles: bool = False,
    ) -> None:
        """Render the configuration preview.

        Parameters
        ----------
        title:
            Window title.
        screenshot_path:
            When provided, save a screenshot of the render to this file path
            instead of opening an interactive window.  Forces off-screen
            rendering so the screenshot can be produced headlessly.
        with_particles:
            When *True*, also run ``generateParticles()`` and overlay generated
            particle clouds (latest step per body). This is optional because
            particle generation can be expensive.
        """
        try:
            import pyvista as pv  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "PyVista is required for visualization.\n"
                "Install it with:  pip install sphinxsim[visualization]"
            ) from None

        ndim = self._spatial_dim()
        # Every preview run rebuilds the current geometry, then falls back to
        # the cached bounds if VTP meshes are unavailable.
        self._shape_bounds_cache = None
        vtp_dir: Path | None = None
        latest_particle_vtps: dict[str, Path] = {}
        vtp_dir = self._try_build_geometries(ndim, with_particles=with_particles)
        if with_particles:
            latest_particle_vtps = self._discover_latest_particle_vtps(vtp_dir)
        self._vtp_dir = vtp_dir

        # Screenshot mode implies off-screen rendering.
        off_screen = self.off_screen or screenshot_path is not None
        plotter = pv.Plotter(title=title, off_screen=off_screen)
        self._populate_plotter(plotter, vtp_dir, latest_particle_vtps)
        self._configure_default_view(plotter, ndim)
        plotter.add_axes()
        plotter.show_grid(font_size=10)

        if vtp_dir:
            mode_label = "VTP geometry"
        elif self._shape_bounds_cache is not None:
            mode_label = "C++ bounds cache"
        else:
            mode_label = "No C++ geometry"
        dim_label = "2-D" if ndim == 2 else "3-D"
        sim_type_label = self.config.simulation_type.value.replace("_", " ").title()
        config_info = f"{dim_label}  •  {sim_type_label}  •  {mode_label}"
        self._add_config_info_text(plotter, config_info, ndim)

        if screenshot_path is not None:
            plotter.screenshot(str(screenshot_path))
        else:
            plotter.show()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _configure_default_view(self, plotter: Any, ndim: int) -> None:
        """Set a sensible initial camera and interaction style."""
        if ndim != 2:
            return

        # Keep 2-D previews in an orthographic XY view to avoid 3-D feel.
        try:
            plotter.enable_2d_style()
        except Exception:
            pass
        try:
            plotter.enable_parallel_projection()
        except Exception:
            pass
        try:
            plotter.view_xy(negative=False)
        except Exception:
            pass

    def _add_config_info_text(self, plotter: Any, config_info: str, ndim: int) -> None:
        """Add the simulation-info overlay text in a non-overlapping location."""
        if ndim == 2:
            plotter.add_text(
                config_info,
                position="upper_edge",
                font_size=10,
                color="cyan",
            )
            return

        plotter.add_text(
            config_info,
            position="upper_edge",
            font_size=10,
            color="cyan",
        )

    def _try_build_geometries(self, ndim: int, with_particles: bool = False) -> Path | None:
        """Run buildGeometries() and return the VTP output directory, or None.

        Writes a temporary runtime JSON derived from ``self.config`` where
        relative input file paths are resolved from ``self.config_path``.
        Geometry generation uses the lightweight ``GeometryBuilder`` class.
        If VTPs are not produced, the builder-provided ``getShapeBounds()``
        cache is reused for preview rendering.
        """
        if self.config_path is None:
            self._shape_bounds_cache = None
            return None

        try:
            sph = load_sphinxsys_core_nd(ndim)
        except ImportError as exc:
            raise ImportError(str(exc)) from None

        vtp_output_dir = self.project_root / ".build-temp" / "test_simulation"
        vtp_output_dir.mkdir(parents=True, exist_ok=True)
        output_subdir = vtp_output_dir / "output"
        for stale_dir in (vtp_output_dir, output_subdir):
            if not stale_dir.is_dir():
                continue
            # In shared output mode, only clean preview geometry meshes.
            for stale_vtp in stale_dir.glob("Shape*.vtp"):
                try:
                    stale_vtp.unlink()
                except OSError:
                    pass

        runtime_config_path: Path | None = None
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="sphinxsim_preview_",
                delete=False,
                dir=str(self.config_path.parent),
            )
            json.dump(self.config.model_dump(exclude_none=True), tmp, indent=2)
            tmp.close()
            runtime_config_path = Path(tmp.name)

            if not with_particles:
                builder = sph.GeometryBuilder(str(runtime_config_path))
                builder.resetInOutputRoot(str(vtp_output_dir))
                builder.buildGeometries()

                try:
                    self._shape_bounds_cache = builder.getShapeBounds()
                except Exception:
                    self._shape_bounds_cache = None

            # Optionally generate particles so preview can overlay the latest
            # body particle clouds if particle_generation is enabled.
            if with_particles and self.config.particle_generation.build_and_run:
                sim = sph.SPHSimulation(str(runtime_config_path))
                sim.resetOutputRoot(str(vtp_output_dir), True)
                sim.buildGeometries()
                sim.generateParticles()

                try:
                    self._shape_bounds_cache = sim.getShapeBounds()
                except Exception:
                    self._shape_bounds_cache = None               
        except Exception:
            self._shape_bounds_cache = None
            return None
        finally:
            if runtime_config_path is not None:
                try:
                    runtime_config_path.unlink()
                except OSError:
                    pass

        # VTPs land in <vtp_output_dir>/output/
        if output_subdir.is_dir() and any(output_subdir.glob("Shape*.vtp")):
            return output_subdir
        if any(vtp_output_dir.glob("Shape*.vtp")):
            return vtp_output_dir

        return None

    def _particle_generation_body_names(self) -> set[str]:
        """Return body names that are expected to have generated particle VTPs."""
        settings = self.config.particle_generation.settings
        if settings is None:
            return set()
        return {body.name for body in settings.bodies}

    def _discover_latest_particle_vtps(self, vtp_dir: Path | None) -> dict[str, Path]:
        """Resolve the latest particle VTP file per body.

        Particle files are expected as ``<BodyName>_<step>.vtp`` and some
        generators use ``<BodyName>_ite_<step>.vtp``. This method returns one
        file per configured particle-generation body with the highest sequence
        number.
        """
        if vtp_dir is None or not vtp_dir.is_dir():
            return {}

        body_names = self._particle_generation_body_names()
        if not body_names:
            return {}

        latest: dict[str, tuple[int, Path]] = {}
        for path in vtp_dir.glob("*.vtp"):
            stem = path.stem

            for body_name in body_names:
                prefix = f"{body_name}_"
                if not stem.startswith(prefix):
                    continue

                suffix = stem[len(prefix):]
                if suffix.startswith("ite_"):
                    suffix = suffix[len("ite_"):]
                if not suffix.isdigit():
                    continue

                step = int(suffix)
                previous = latest.get(body_name)
                if previous is None or step >= previous[0]:
                    latest[body_name] = (step, path)
                break

        return {body_name: item[1] for body_name, item in latest.items()}

    def _populate_plotter(
        self,
        plotter: Any,
        vtp_dir: Path | None,
        latest_particle_vtps: dict[str, Path] | None = None,
    ) -> None:
        """Add all shapes and annotations to *plotter*."""
        import pyvista as pv  # type: ignore[import]

        from sphinxsim.visualization.annotations import (
            body_constraint_label,
            body_label,
            gravity_label,
            observer_label,
            oriented_box_label,
        )

        config = self.config
        self._annotation_label_actors = []
        hide_shapes = bool(latest_particle_vtps)

        def _add_annotation_label(
            points: list[tuple[float, float, float]] | list[Any],
            labels: list[str],
            *,
            font_size: int,
            text_color: str,
        ) -> None:
            def _deconflict_anchor(anchor: tuple[float, float, float]) -> tuple[float, float, float]:
                if not occupied_points:
                    return anchor

                if scene_bounds is not None and len(scene_bounds) == 6:
                    ex = max(scene_bounds[1] - scene_bounds[0], 1e-3)
                    ey = max(scene_bounds[3] - scene_bounds[2], 1e-3)
                    ez = max(scene_bounds[5] - scene_bounds[4], 1e-3)
                    max_extent = max(ex, ey, ez)
                else:
                    max_extent = 1.0

                min_clearance = max(0.035 * max_extent, 1e-3)
                min_clearance_sq = min_clearance * min_clearance

                def _distance_sq(p: tuple[float, float, float], q: tuple[float, float, float]) -> float:
                    dx = p[0] - q[0]
                    dy = p[1] - q[1]
                    dz = p[2] - q[2]
                    return dx * dx + dy * dy + dz * dz

                current_min_sq = min(_distance_sq(anchor, q) for q in occupied_points)
                if current_min_sq >= min_clearance_sq:
                    return anchor

                step = max(0.03 * max_extent, 5e-4)
                offsets = [
                    (0.0, 0.0, 0.0),
                    (step, 0.0, 0.0),
                    (-step, 0.0, 0.0),
                    (0.0, step, 0.0),
                    (0.0, -step, 0.0),
                    (step, step, 0.0),
                    (-step, step, 0.0),
                    (step, -step, 0.0),
                    (-step, -step, 0.0),
                    (2.0 * step, 0.0, 0.0),
                    (0.0, 2.0 * step, 0.0),
                ]

                best_point = anchor
                best_score = current_min_sq
                for ox, oy, oz in offsets:
                    candidate = (anchor[0] + ox, anchor[1] + oy, anchor[2] + oz)

                    if scene_bounds is not None and len(scene_bounds) == 6:
                        margin_x = 0.01 * max(scene_bounds[1] - scene_bounds[0], 1.0)
                        margin_y = 0.01 * max(scene_bounds[3] - scene_bounds[2], 1.0)
                        margin_z = 0.01 * max(scene_bounds[5] - scene_bounds[4], 1.0)
                        candidate = (
                            min(max(candidate[0], scene_bounds[0] + margin_x), scene_bounds[1] - margin_x),
                            min(max(candidate[1], scene_bounds[2] + margin_y), scene_bounds[3] - margin_y),
                            min(max(candidate[2], scene_bounds[4] + margin_z), scene_bounds[5] - margin_z),
                        )

                    candidate_min_sq = min(_distance_sq(candidate, q) for q in occupied_points)
                    if candidate_min_sq > best_score:
                        best_score = candidate_min_sq
                        best_point = candidate

                return best_point

            normalized_points: list[tuple[float, float, float]] = []
            for point in points:
                try:
                    values = [float(v) for v in point]
                except Exception:
                    continue
                if len(values) == 2:
                    normalized_points.append((values[0], values[1], 0.0))
                elif len(values) >= 3:
                    normalized_points.append((values[0], values[1], values[2]))

            if not normalized_points:
                return

            normalized_points = [_deconflict_anchor(p) for p in normalized_points]
            occupied_points.extend(normalized_points)

            text_values = [str(label) for label in labels]
            actor = plotter.add_point_labels(
                normalized_points,
                text_values,
                point_size=0,
                font_size=font_size,
                text_color=text_color,
                always_visible=True,
            )
            if actor is not None:
                self._annotation_label_actors.append(
                    {
                        "actor": actor,
                        "font_size": int(font_size),
                        "points": normalized_points,
                        "labels": text_values,
                        "text_color": text_color,
                    }
                )

        # Build a name → colour map for body shapes
        body_names: set[str] = set()
        body_names.update(b.name for b in config.fluid_bodies)
        body_names.update(b.name for b in config.solid_bodies)
        body_names.update(b.name for b in config.continuum_bodies)

        rendered_shapes: set[str] = set()

        # Build a name → shape lookup for later use (constraint labels, etc.)
        shape_lookup: dict[str, "ShapeConfig"] = {
            shape.name: shape for shape in config.geometries.shapes
        }

        occupied_points: list[tuple[float, float, float]] = []
        scene_bounds: list[float] | None = None

        def _update_scene_bounds(mesh: Any) -> None:
            nonlocal scene_bounds
            try:
                bx = [float(v) for v in mesh.bounds]
            except Exception:
                return
            if len(bx) != 6:
                return
            if scene_bounds is None:
                scene_bounds = bx[:]
                return
            scene_bounds[0] = min(scene_bounds[0], bx[0])
            scene_bounds[1] = max(scene_bounds[1], bx[1])
            scene_bounds[2] = min(scene_bounds[2], bx[2])
            scene_bounds[3] = max(scene_bounds[3], bx[3])
            scene_bounds[4] = min(scene_bounds[4], bx[4])
            scene_bounds[5] = max(scene_bounds[5], bx[5])

        # --- Render each shape ---
        if not hide_shapes:
            for shape in config.geometries.shapes:
                if shape.type.value == "extrude_shape" or shape.type.value == "complex_shape":
                    # Skip — rendered via sub-shapes
                    continue

                mesh = self._load_shape_mesh(shape, vtp_dir, config)
                if mesh is None:
                    continue

                is_body = shape.name in body_names
                colour = _body_colour(shape.name, config)
                opacity = 0.6 if is_body else 0.35
                style = "surface" if is_body else "wireframe"

                plotter.add_mesh(
                    mesh,
                    color=colour,
                    opacity=opacity,
                    style=style,
                    label=shape.name,
                )
                _update_scene_bounds(mesh)

                label_anchor = _label_anchor_point(mesh)
                label_text = body_label(shape.name, config) if is_body else shape.name
                _add_annotation_label(
                    [label_anchor],
                    [label_text],
                    font_size=8,
                    text_color="white",
                )
                occupied_points.append(
                    (
                        float(mesh.center[0]),
                        float(mesh.center[1]),
                        float(mesh.center[2]) if len(mesh.center) > 2 else 0.0,
                    )
                )
                rendered_shapes.add(shape.name)

        # --- Overlay latest generated particles (one VTP per body) ---
        particle_vtps = latest_particle_vtps or {}
        for body_name in sorted(particle_vtps):
            vtp_path = particle_vtps[body_name]
            try:
                particle_mesh = pv.read(str(vtp_path))
            except Exception:
                continue

            plotter.add_mesh(
                particle_mesh,
                color=_body_colour(body_name, config),
                opacity=0.95,
                style="points",
                point_size=_PARTICLE_POINT_SIZE,
                render_points_as_spheres=True,
                label=f"Particles: {body_name}",
            )

            step_text = vtp_path.stem.rsplit("_", 1)[-1]
            _add_annotation_label(
                [particle_mesh.center],
                [f"Particles: {body_name} (step {step_text})"],
                font_size=7,
                text_color="white",
            )
            _update_scene_bounds(particle_mesh)
            occupied_points.append(
                (
                    float(particle_mesh.center[0]),
                    float(particle_mesh.center[1]),
                    float(particle_mesh.center[2]) if len(particle_mesh.center) > 2 else 0.0,
                )
            )

        # --- Render oriented boxes (in/outlets and constraint regions) ---
        for ob in config.geometries.oriented_boxes:
            mesh = self._load_oriented_box_mesh(ob, vtp_dir)
            if mesh is None:
                continue

            colour = _INLET_OUTLET_COLOUR if ob.type.value == "boundary" else _REGION_COLOUR
            plotter.add_mesh(
                mesh,
                color=colour,
                opacity=0.50,
                style="wireframe",
                line_width=2,
                label=ob.name,
            )
            _update_scene_bounds(mesh)
            label_text = oriented_box_label(ob, config)
            _add_annotation_label(
                [mesh.center],
                [label_text],
                font_size=7,
                text_color="yellow",
            )
            occupied_points.append(
                (
                    float(mesh.center[0]),
                    float(mesh.center[1]),
                    float(mesh.center[2]) if len(mesh.center) > 2 else 0.0,
                )
            )

        # --- Body constraints ---
        # For constraints with a *region* (oriented box reference) we overlay
        # the referenced box mesh with the constraint colour.  For constraints
        # without a region the label is placed at the centroid of the
        # constrained body's shape mesh (if available).
        oriented_box_lookup = {
            ob.name: ob for ob in config.geometries.oriented_boxes
        }
        for constraint in config.body_constraints:
            label_text = body_constraint_label(constraint)

            if constraint.region is not None and constraint.region in oriented_box_lookup:
                ob = oriented_box_lookup[constraint.region]
                mesh = self._load_oriented_box_mesh(ob, vtp_dir)
                if mesh is not None:
                    plotter.add_mesh(
                        mesh,
                        color=_CONSTRAINT_COLOUR,
                        opacity=0.30,
                        style="wireframe",
                        line_width=3,
                        label=f"Constraint: {constraint.body_name}",
                    )
                    _add_annotation_label(
                        [mesh.center],
                        [label_text],
                        font_size=7,
                        text_color="orange",
                    )
            else:
                # No region — try to label at the body shape centroid.
                shape = shape_lookup.get(constraint.body_name)
                if shape is not None:
                    mesh = self._load_shape_mesh(shape, vtp_dir, config)
                    if mesh is not None:
                        _add_annotation_label(
                            [mesh.center],
                            [label_text],
                            font_size=7,
                            text_color="orange",
                        )

        # --- Domain bounding box ---
        if config.geometries.system_domain is not None:
            domain = config.geometries.system_domain
            domain_mesh = _bounds_to_box(domain.lower_bound, domain.upper_bound)
            plotter.add_mesh(
                domain_mesh,
                color="white",
                opacity=0.10,
                style="wireframe",
                line_width=1,
            )
            _update_scene_bounds(domain_mesh)

        # --- Gravity annotation ---
        self._add_gravity_arrow(
            plotter,
            config,
            pv,
            occupied_points,
            scene_bounds=tuple(scene_bounds) if scene_bounds is not None else None,
        )

        # --- Observer positions ---
        for observer in config.observers:
            if not observer.positions:
                continue

            points: list[tuple[float, float, float]] = []
            for position in observer.positions:
                if len(position) == 2:
                    points.append((float(position[0]), float(position[1]), 0.0))
                elif len(position) == 3:
                    points.append((float(position[0]), float(position[1]), float(position[2])))

            if not points:
                continue

            observer_points = pv.PolyData(points)
            plotter.add_mesh(
                observer_points,
                color=_OBSERVER_COLOUR,
                point_size=10,
                render_points_as_spheres=True,
                label=f"Observer: {observer.name}",
            )
            _add_annotation_label(
                [points[0]],
                [observer_label(observer)],
                font_size=7,
                text_color="magenta",
            )

        # --- Legend ---
        legend_entries = []
        if not hide_shapes:
            legend_entries.extend(
                [
                    ["Fluid body", _FLUID_COLOUR],
                    ["Solid body", _SOLID_COLOUR],
                    ["Continuum body", _CONTINUUM_COLOUR],
                    ["Other shape", _UNKNOWN_COLOUR],
                ]
            )
        if particle_vtps:
            legend_entries.append(["Generated particles", (1.0, 1.0, 1.0)])
        legend_entries.extend(
            [
                ["Inlet/Outlet", _INLET_OUTLET_COLOUR],
                ["Region", _REGION_COLOUR],
                ["Observer", _OBSERVER_COLOUR],
                ["Constraint", _CONSTRAINT_COLOUR],
                ["Gravity", _GRAVITY_COLOUR],
            ]
        )
        plotter.add_legend(
            [
                (entry[0], [int(c * 255) for c in entry[1]])
                for entry in legend_entries
            ],
            size=(0.16, 0.16),
            bcolor="black",
            border=True,
        )

    def _add_gravity_arrow(
        self,
        plotter: Any,
        config: "SimulationConfig",
        pv: Any,
        occupied_points: list[tuple[float, float, float]] | None = None,
        scene_bounds: tuple[float, ...] | None = None,
    ) -> None:
        """Render gravity as a directional arrow with a text label.

        In 2-D, the arrow is anchored near the lower-left of the scene so it is
        easier to spot and appears close to the on-screen axes widget. In 3-D,
        it originates near the upper-left/front of the domain bounding box. Its
        length is scaled to a fraction of the domain size so it remains visible
        regardless of scene scale. When gravity is unset, nothing is rendered.
        """
        from sphinxsim.visualization.annotations import gravity_label

        g_label = gravity_label(config)
        if g_label is None:
            return

        g = config.gravity
        ndim = len(g)

        # Determine a scene-appropriate arrow length from the domain bounds.
        domain = config.geometries.system_domain
        if domain is not None:
            lower = list(domain.lower_bound)
            upper = list(domain.upper_bound)
        elif scene_bounds is not None and len(scene_bounds) == 6:
            lower = [float(scene_bounds[0]), float(scene_bounds[2])]
            upper = [float(scene_bounds[1]), float(scene_bounds[3])]
            if ndim == 3:
                lower.append(float(scene_bounds[4]))
                upper.append(float(scene_bounds[5]))
        else:
            # Fall back to the extent of all shape bounds if available.
            lower, upper = self._scene_extent(ndim)

        extent = [upper[i] - lower[i] for i in range(ndim)]
        max_extent = max(extent) if extent else 1.0
        if max_extent <= 0:
            max_extent = 1.0
        arrow_length = 0.25 * max_extent

        # Gravity direction (unit vector).  PyVista's Arrow always requires a
        # 3-D direction vector, so pad 2-D gravity with a zero z-component.
        magnitude = sum(c * c for c in g) ** 0.5
        if magnitude == 0:
            return
        direction = tuple(c / magnitude for c in g)
        if ndim == 2:
            direction = direction + (0.0,)

        # Arrow start point:
        # - 2-D: choose from corner candidates and prefer visually empty space.
        # - 3-D: keep upper-left/front to avoid clutter in perspective view.
        if ndim == 2:
            candidates = [
                (lower[0] + 0.10 * extent[0], lower[1] + 0.18 * extent[1], 0.0),
                (lower[0] + 0.10 * extent[0], upper[1] - 0.18 * extent[1], 0.0),
                (upper[0] - 0.10 * extent[0], lower[1] + 0.18 * extent[1], 0.0),
                (upper[0] - 0.10 * extent[0], upper[1] - 0.18 * extent[1], 0.0),
            ]

            points = occupied_points or []

            def _score(candidate: tuple[float, float, float]) -> float:
                # Prefer larger distance from rendered geometry centers.
                if points:
                    nearest = min(
                        (candidate[0] - p[0]) ** 2 + (candidate[1] - p[1]) ** 2
                        for p in points
                    )
                else:
                    nearest = 0.0

                # Prefer starts whose arrow end remains within scene bounds.
                end_x = candidate[0] + direction[0] * arrow_length
                end_y = candidate[1] + direction[1] * arrow_length
                margin_x = 0.03 * extent[0]
                margin_y = 0.03 * extent[1]
                inside = (
                    lower[0] + margin_x <= end_x <= upper[0] - margin_x
                    and lower[1] + margin_y <= end_y <= upper[1] - margin_y
                )
                return nearest + (1e6 if inside else 0.0)

            start = max(candidates, key=_score)
        else:
            start = (lower[0] + 0.05 * extent[0], upper[1] - 0.05 * extent[1], upper[2] - 0.05 * extent[2])

        arrow = pv.Arrow(start=start, direction=direction, scale=arrow_length)
        plotter.add_mesh(
            arrow,
            color=_GRAVITY_COLOUR,
            line_width=4,
            label="Gravity",
        )

        # Place the gravity text label just above the arrow start.
        label_offset = 0.03 * max_extent
        if ndim == 2:
            label_pos = (start[0], start[1] + label_offset, 0.0)
        else:
            label_pos = (start[0], start[1] + label_offset, start[2])

        try:
            actor = plotter.add_point_labels(
                [label_pos],
                [g_label],
                point_size=0,
                font_size=9,
                text_color="cyan",
                always_visible=True,
            )
            if actor is not None:
                self._annotation_label_actors.append(
                    {
                        "actor": actor,
                        "font_size": 9,
                        "points": [label_pos],
                        "labels": [g_label],
                        "text_color": "cyan",
                    }
                )
        except Exception:
            # Fall back to corner text if point labels are unavailable.
            plotter.add_text(g_label, position="lower_left", font_size=9, color="cyan")

    def _scene_extent(self, ndim: int) -> tuple[list[float], list[float]]:
        """Return coarse lower/upper bounds for the scene.

        Used when ``system_domain`` is absent so the gravity arrow can still be
        scaled to the scene.  Falls back to a unit box if no bounds are preview
        available.
        """
        lower = [0.0] * ndim
        upper = [1.0] * ndim

        if self._shape_bounds_cache is not None:
            for bounds in self._shape_bounds_cache.values():
                lo, hi = list(bounds[0]), list(bounds[1])
                for i in range(min(ndim, len(lo))):
                    lower[i] = min(lower[i], lo[i])
                    upper[i] = max(upper[i], hi[i])

        # Pad slightly so the arrow sits just inside the scene.
        for i in range(ndim):
            pad = 0.05 * max(upper[i] - lower[i], 1.0)
            lower[i] -= pad
            upper[i] += pad

        return lower, upper

    def _load_shape_mesh(
        self,
        shape: "ShapeConfig",
        vtp_dir: Path | None,
        config: "SimulationConfig",
    ) -> Any | None:
        """Load the mesh for *shape* — VTP first, C++ bounds second."""
        if vtp_dir is not None:
            vtp_path = vtp_dir / f"Shape{shape.name}.vtp"
            if vtp_path.exists():
                try:
                    import pyvista as pv  # type: ignore[import]
                    return pv.read(str(vtp_path))
                except Exception:
                    pass

        if self._shape_bounds_cache is not None and shape.name in self._shape_bounds_cache:
            lower, upper = self._shape_bounds_cache[shape.name]
            return _bounds_to_box(list(lower), list(upper))

        return None

    def _load_oriented_box_mesh(
        self, ob: "OrientedBoxConfig", vtp_dir: Path | None
    ) -> Any | None:
        """Load the oriented-box VTP written by addOrientedBox()."""
        if vtp_dir is None:
            return None
        vtp_path = vtp_dir / f"Shape{ob.name}.vtp"
        if not vtp_path.exists():
            return None
        try:
            import pyvista as pv  # type: ignore[import]
            return pv.read(str(vtp_path))
        except Exception:
            return None
