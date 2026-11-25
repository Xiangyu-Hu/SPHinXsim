"""Persistent interactive preview runtime and lifecycle management."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import warnings
from pathlib import Path
from threading import Thread
from typing import Any

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_editor import JsonEditorMixin
from sphinxsim.llm.common import dump_simulation_config_json


def _find_project_root(start: str | None = None) -> str:
    start = start or str(Path(__file__).resolve().parent)
    current = start
    while current != os.path.dirname(current):
        if os.path.exists(os.path.join(current, "pyproject.toml")) and os.path.isfile(
            os.path.join(current, "sphinxsim", "cli.py")
        ):
            return current
        current = os.path.dirname(current)
    return os.getcwd()


PROJECT_ROOT = Path(_find_project_root())
_LIVE_PREVIEW_MAX_FPS = 25
_LIVE_REFRESH_INTERVAL_MS = 75


def _write_validated_config(
    path: Path, config: SimulationConfig, content: str | None = None
) -> str:
    """Atomically replace *path* with *content* (default: canonical validated JSON)."""
    if content is None:
        content = dump_simulation_config_json(config, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".json",
            prefix=f".{path.stem}.",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return content


class _ShellPreviewRuntime(JsonEditorMixin):
    """Persistent shell preview runtime.

    Keeps a non-blocking plotter window alive across `preview` commands and
    updates its scene in place.
    """

    def __init__(self, *, config_type=None, write_config=None) -> None:
        self.config_type = config_type or SimulationConfig
        self.write_config = write_config or _write_validated_config
        self.plotter: Any | None = None
        self.last_signature: str | None = None
        self._using_background_plotter = False
        self._hover_interactor: Any | None = None
        self._hover_observer_tag: Any | None = None
        self._annotation_click_observer_tag: Any | None = None
        self._json_editor: Any | None = None
        self._particle_visibility: Any | None = None
        self._particle_menu_filter: Any | None = None
        self._particle_time_display: Any | None = None
        self._particle_scene_path: Path | None = None
        self._particle_scene_generation = 0
        self._particle_scene_config_signature: str | None = None
        self._pending_simulation: Any | None = None
        self._pending_runtime_config_path: Path | None = None
        self._pending_initialized = False
        self._live_run_thread: Thread | None = None
        self._live_refresh_timer: Any | None = None
        self._live_output_dir: Path | None = None
        self._live_particle_body_names: set[str] = set()
        self._live_particle_colours: dict[str, Any] = {}
        self._live_particle_versions: dict[str, Any] = {}
        self._live_result_catalog: Any | None = None
        self._live_playback_queue: list[tuple[float, dict[str, Path]]] = []
        self._live_replay_mode = False
        self._animation_output_dir: Path | None = None
        self._animation_particle_body_names: set[str] = set()
        self._animation_particle_colours: dict[str, Any] = {}
        self._animation_config: SimulationConfig | None = None
        self._animation_config_path: Path | None = None
        self._animation_run_completed = False

    def _reset_preview_state(self) -> None:
        """Forget all state associated with a closed preview window."""
        self._remove_hover_observer()
        self._stop_live_refresh()
        self.discard_pending_simulation()
        self.plotter = None
        self._particle_time_display = None
        self._json_editor = None
        self._particle_visibility = None
        self._particle_menu_filter = None
        self._particle_scene_path = None
        self._particle_scene_config_signature = None
        self.last_signature = None
        self._using_background_plotter = False

    def _on_preview_window_closed(self, *_args: Any) -> None:
        """Handle a user closing the Qt window outside the shell command loop."""
        self._reset_preview_state()

    def _watch_preview_window_close(self) -> None:
        """Synchronize pyvistaqt's native close signal with this runtime."""
        if self.plotter is None:
            return
        app_window = getattr(self.plotter, "app_window", None)
        signal_close = getattr(app_window, "signal_close", None)
        if signal_close is None:
            return
        try:
            signal_close.connect(self._on_preview_window_closed)
        except Exception:
            pass

    def close(self) -> None:
        plotter = self.plotter
        if plotter is not None:
            try:
                plotter.close()
            except Exception:
                pass
        self._reset_preview_state()

    def discard_pending_simulation(self) -> None:
        """Release a simulation retained by ``preview --with-particles``."""
        self._pending_simulation = None
        self._pending_initialized = False
        runtime_config_path = self._pending_runtime_config_path
        self._pending_runtime_config_path = None
        if runtime_config_path is not None:
            try:
                runtime_config_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stop_live_refresh(self, *, preserve_context: bool = False) -> None:
        timer = self._live_refresh_timer
        self._live_refresh_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if not preserve_context:
            self._live_output_dir = None
            self._live_particle_body_names = set()
            self._live_particle_colours = {}
        self._live_result_catalog = None
        self._live_particle_versions = {}
        self._live_playback_queue = []
        self._live_replay_mode = False

    def _refresh_live_particles(self) -> None:
        """Refresh the latest body snapshots or advance an explicitly requested replay."""
        if self.plotter is None or self._live_output_dir is None:
            return

        try:
            import pyvista  # noqa: F401
        except ImportError:
            return

        if not self._live_replay_mode:
            from sphinxsim.visualization.live_particles import refresh_particle_actors

            if self._live_result_catalog is None:
                from sphinxsim.results import ResultCatalog

                self._live_result_catalog = ResultCatalog()
            refresh_particle_actors(
                self.plotter,
                self._live_output_dir,
                self._live_particle_colours,
                self._live_particle_versions,
                self._live_result_catalog,
                self._particle_visibility,
                self._particle_time_display,
            )
            thread = self._live_run_thread
            if thread is not None and not thread.is_alive():
                self._stop_live_refresh()
                self._live_run_thread = None
            return

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh(preserve_context=True)
            return

        _, body_snapshots = self._live_playback_queue.pop(0)
        for body_name, vtp_path in body_snapshots.items():
            try:
                from sphinxsim.visualization.preview import (
                    _PARTICLE_DEFAULT_OPACITY, _PARTICLE_POINT_SIZE, _particle_label,
                )

                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*vtkXMLPolyDataReader.*")
                    import pyvista as pv
                    mesh = pv.read(str(vtp_path))
            except Exception:
                continue
            if getattr(mesh, "n_points", None) == 0:
                continue
            actor = self.plotter.add_mesh(
                mesh,
                name=f"particle-preview-{body_name}",
                color=self._live_particle_colours.get(body_name),
                opacity=_PARTICLE_DEFAULT_OPACITY,
                style="points",
                point_size=_PARTICLE_POINT_SIZE,
                label=_particle_label(body_name),
                reset_camera=False,
            )
            generation = vtp_path.stem[len(body_name) + 1:].startswith("ite_")
            if self._particle_visibility is not None:
                self._particle_visibility.add_actor(body_name, actor, mesh, initialized=not generation)
            self._update_particle_time(body_name, mesh, generation=generation)

        if self._live_replay_mode and not self._live_playback_queue:
            self._stop_live_refresh(preserve_context=True)
            print("✅ Particle animation replay completed.")

    def _start_live_refresh(self) -> None:
        timer = self._live_refresh_timer
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._live_refresh_timer = None
        self._live_particle_versions = {}
        from sphinxsim.results import ResultCatalog

        self._live_result_catalog = ResultCatalog()
        self._live_playback_queue = []
        self._live_replay_mode = False
        if not self._using_background_plotter or self.plotter is None:
            return
        try:
            from qtpy import QtCore
        except ImportError:
            return
        timer = QtCore.QTimer()
        timer.setInterval(_LIVE_REFRESH_INTERVAL_MS)
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer

    def replay_animation(self, *, relaxation: bool = False, _automatic: bool = False) -> int:
        """Replay simulation or relaxation snapshots at no more than 25 FPS."""
        def unavailable(message: str) -> int:
            prefix = "⚠️ Relaxation playback unavailable:" if _automatic else "❌"
            print(f"{prefix} {message}", file=sys.stderr)
            return 1

        thread = self._live_run_thread
        if thread is not None and thread.is_alive():
            return unavailable("Wait for the simulation to finish before replaying.")
        if not relaxation and not self._animation_run_completed:
            return unavailable("Complete a successful animated run before replaying.")
        if self._animation_output_dir is None or not self._animation_particle_body_names:
            return unavailable(
                "No particle animation is available. Run 'run --with-animation', "
                "or run 'preview --with-particles', 'initialize-simulation', "
                "and 'continue-to-run'."
            )
        snapshots: dict[float, dict[str, Path]] = {}
        for vtp_path in self._animation_output_dir.glob("*.vtp"):
            for body_name in sorted(self._animation_particle_body_names, key=len, reverse=True):
                prefix = f"{body_name}_"
                if not vtp_path.stem.startswith(prefix):
                    continue
                suffix = vtp_path.stem[len(prefix):]
                generation = suffix.startswith("ite_")
                if generation != relaxation:
                    break
                try:
                    sequence = float(suffix[4:] if generation else suffix)
                except ValueError:
                    break
                if math.isfinite(sequence):
                    snapshots.setdefault(sequence, {})[body_name] = vtp_path
                break

        if not snapshots:
            kind = "relaxation" if relaxation else "simulation"
            return unavailable(f"No {kind} particle snapshots found in {self._animation_output_dir}.")

        if self.plotter is None:
            if self._animation_config is None or self._animation_config_path is None:
                return unavailable("The configuration for this particle animation is unavailable.")
            rc = self.show_or_update(
                self._animation_config,
                resolved_config_path=self._animation_config_path,
                with_particles=False,
                force=True,
            )
            if rc != 0:
                return rc
        if not self._using_background_plotter:
            return unavailable("Animation replay requires the Qt-backed persistent preview.")

        try:
            from qtpy import QtCore
        except ImportError as exc:
            return unavailable(
                f"Animation replay requires Qt (`pyvistaqt` with `PySide6` or `PyQt5`): {exc}"
            )

        self._stop_live_refresh()
        self._live_output_dir = self._animation_output_dir
        self._live_particle_body_names = set(self._animation_particle_body_names)
        self._live_particle_colours = dict(self._animation_particle_colours)
        self._live_particle_versions = {
            body_name: float("-inf")
            for body_name in self._live_particle_body_names
        }
        self._live_playback_queue = [
            (sequence, body_snapshots)
            for sequence, body_snapshots in sorted(snapshots.items())
        ]
        self._live_replay_mode = True
        timer = QtCore.QTimer()
        timer.setInterval(round(1000 / _LIVE_PREVIEW_MAX_FPS))
        timer.timeout.connect(self._refresh_live_particles)
        timer.start()
        self._live_refresh_timer = timer
        print(
            f"▶️ Replaying {len(self._live_playback_queue)} particle frames "
            f"at up to {_LIVE_PREVIEW_MAX_FPS} FPS."
        )
        return 0

    def initialize_simulation(self) -> int:
        """Build and initialize the retained simulation without running it."""
        simulation = self._pending_simulation
        if simulation is None:
            print(
                "No particle-preview simulation is available. Run 'preview --with-particles' first.",
                file=sys.stderr,
            )
            return 1
        if self._pending_initialized:
            print("Simulation is already initialized. Run 'continue-to-run' to start it.", file=sys.stderr)
            return 1

        self._stop_live_refresh(preserve_context=True)
        try:
            self._animation_run_completed = False
            simulation.buildSimulation()
            print("✅ Simulation built")
            simulation.initializeSimulation()
            print("✅ Simulation initialized")
        except Exception as exc:
            self.discard_pending_simulation()
            print(f"❌ Simulation initialization failed: {exc}", file=sys.stderr)
            return 1

        self._pending_initialized = True
        if self.plotter is not None:
            try:
                self._show_initialized_particles()
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                print(f"❌ Could not display initialized particles: {exc}", file=sys.stderr)
                return 1
        return 0

    def _show_initialized_particles(self) -> None:
        from sphinxsim.results import ResultCatalog, read_particle_mesh
        from sphinxsim.visualization.preview import (
            _PARTICLE_DEFAULT_OPACITY, _PARTICLE_POINT_SIZE, _particle_label,
        )

        if self._live_output_dir is None:
            raise RuntimeError("The simulation output directory is unavailable.")
        frames = ResultCatalog().latest(self._live_output_dir, sorted(self._live_particle_body_names))
        if not frames:
            raise RuntimeError(f"No initial body-state snapshots found in {self._live_output_dir}.")
        for body_name, frame in frames.items():
            mesh = read_particle_mesh(frame.path)
            actor = self.plotter.add_mesh(
                mesh,
                name=f"particle-preview-{body_name}",
                color=self._live_particle_colours.get(body_name),
                opacity=_PARTICLE_DEFAULT_OPACITY,
                style="points",
                point_size=_PARTICLE_POINT_SIZE,
                label=_particle_label(body_name),
                reset_camera=False,
            )
            if self._particle_visibility is not None:
                self._particle_visibility.add_actor(body_name, actor, mesh, initialized=True)
            self._update_particle_time(body_name, mesh)
        self.plotter.render()

    def _update_particle_time(self, body: str, mesh: Any, *, generation: bool = False) -> None:
        if self._particle_time_display is None:
            from sphinxsim.visualization.particle_time import ParticleTimeDisplay

            self._particle_time_display = ParticleTimeDisplay(self.plotter)
        self._particle_time_display.update(body, mesh, generation=generation)

    def continue_to_run(self) -> int:
        """Run the initialized simulation and refresh the latest particle snapshots."""
        simulation = self._pending_simulation
        if simulation is None or not self._pending_initialized:
            print(
                "No initialized simulation is available. Run 'preview --with-particles' "
                "then 'initialize-simulation' first.",
                file=sys.stderr,
            )
            return 1
        runtime_config_path = self._pending_runtime_config_path
        self._pending_simulation = None
        self._pending_runtime_config_path = None
        self._pending_initialized = False

        try:
            print("\n🚀 Running simulation...")
            self._start_live_refresh()

            def run_simulation() -> None:
                try:
                    simulation.run()
                    self._animation_run_completed = True
                    print("✅ Simulation completed successfully!")
                except Exception as exc:
                    print(f"❌ Continued run failed: {exc}", file=sys.stderr)
                finally:
                    if runtime_config_path is not None:
                        try:
                            runtime_config_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            self._live_run_thread = Thread(target=run_simulation, daemon=True)
            self._live_run_thread.start()
            print(
                f"ℹ️ Preview checks for the latest body snapshots every "
                f"{_LIVE_REFRESH_INTERVAL_MS} ms."
            )
            return 0
        except Exception as exc:
            print(f"❌ Continued run failed: {exc}", file=sys.stderr)
            return 1
        finally:
            if self._live_run_thread is None:
                del simulation
                if runtime_config_path is not None:
                    try:
                        runtime_config_path.unlink(missing_ok=True)
                    except OSError:
                        pass


    def pump_ui_events(self) -> None:
        """Keep the persistent preview responsive while shell work blocks."""
        if self.plotter is None:
            return

        try:
            if self._using_background_plotter:
                app = getattr(self.plotter, "app", None)
                if app is not None and hasattr(app, "processEvents"):
                    app.processEvents()
                    return

                try:
                    from PySide6.QtWidgets import QApplication  # type: ignore[import]
                except Exception:
                    try:
                        from PyQt5.QtWidgets import QApplication  # type: ignore[import]
                    except Exception:
                        QApplication = None  # type: ignore[misc,assignment]

                if QApplication is not None:
                    app = QApplication.instance()
                    if app is not None:
                        app.processEvents()
                        return

            if hasattr(self.plotter, "render"):
                self.plotter.render()
        except Exception:
            pass

    @staticmethod
    def _set_label_font_size(actor: Any, size: int) -> bool:
        try:
            mapper = actor.GetMapper()
            if hasattr(mapper, "GetLabelTextProperty"):
                text_prop = mapper.GetLabelTextProperty()
            else:
                connection = mapper.GetInputConnection(0, 0)
                text_prop = connection.GetProducer().GetTextProperty()
            text_prop.SetFontSize(int(size))
            if hasattr(mapper, "Modified"):
                mapper.Modified()
            if hasattr(actor, "Modified"):
                actor.Modified()
            return True
        except Exception:
            return False

    def _rebuild_label_actor(self, entry: dict[str, Any], font_size: int) -> Any | None:
        if self.plotter is None:
            return entry.get("actor")

        actor = entry.get("actor")
        get_visibility = getattr(actor, "GetVisibility", None)
        visible = bool(get_visibility()) if callable(get_visibility) else True
        if actor is not None:
            try:
                self.plotter.remove_actor(actor, render=False)
            except TypeError:
                try:
                    self.plotter.remove_actor(actor)
                except Exception:
                    pass
            except Exception:
                pass

        points = entry.get("points")
        labels = entry.get("labels")
        text_color = entry.get("text_color", "white")
        if not points or not labels:
            return actor

        try:
            new_actor = self.plotter.add_point_labels(
                points,
                labels,
                point_size=0,
                font_size=int(font_size),
                text_color=text_color,
                always_visible=True,
                **entry.get("label_options", {}),
            )
        except Exception:
            return actor

        if new_actor is not None:
            new_actor.SetVisibility(visible)
        entry["actor"] = new_actor
        return new_actor

    def _remove_hover_observer(self) -> None:
        if self._hover_interactor is not None:
            for tag in (
                self._hover_observer_tag,
                self._annotation_click_observer_tag,
            ):
                if tag is None:
                    continue
                try:
                    self._hover_interactor.RemoveObserver(tag)
                except Exception:
                    pass
        self._hover_interactor = None
        self._hover_observer_tag = None
        self._annotation_click_observer_tag = None

    def _install_annotation_hover(self, visualizer: Any) -> None:
        self._remove_hover_observer()

        if self.plotter is None:
            return

        # Hover effects are intended for the persistent Qt-backed preview.
        # The plain Plotter fallback can become less responsive under extra
        # interactor callbacks while the shell waits for input.
        if not self._using_background_plotter:
            return

        entries = getattr(visualizer, "annotation_label_actors", None) or []
        if not entries:
            return

        iren_wrapper = getattr(self.plotter, "iren", None)
        interactor = getattr(iren_wrapper, "interactor", None)
        renderer = getattr(self.plotter, "renderer", None)
        if interactor is None or renderer is None:
            return

        try:
            import vtk  # type: ignore[import]
        except Exception:
            return

        spec_entries: list[dict[str, Any]] = []
        hover_coordinate = vtk.vtkCoordinate()
        hover_coordinate.SetCoordinateSystemToWorld()
        hover_installed = False

        def _apply_size(spec_index: int, font_size: int) -> None:
            entry = spec_entries[spec_index]
            actor = entry.get("actor")
            if actor is not None and self._set_label_font_size(actor, font_size):
                return

            rebuilt_actor = self._rebuild_label_actor(entry, font_size)
            if rebuilt_actor is not None:
                entry["actor"] = rebuilt_actor
                if hover_installed:
                    editor = self._json_editor
                    set_elements = editor.get("set_preview_elements") if isinstance(editor, dict) else None
                    elements = getattr(visualizer, "preview_element_actors", None)
                    if callable(set_elements) and isinstance(elements, dict):
                        set_elements(elements)

        for entry in entries:
            actor = entry.get("actor") if isinstance(entry, dict) else None
            if actor is None:
                continue
            base_size = int(entry.get("font_size", 8))
            hover_size = max(base_size + 4, 12)
            spec_index = len(spec_entries)
            entry["base_size"] = base_size
            entry["hover_size"] = hover_size
            spec_entries.append(entry)
            _apply_size(spec_index, base_size)

        if not spec_entries:
            return

        active_spec_index: int | None = None

        def _estimate_label_bounds(entry: dict[str, Any], dx: float, dy: float) -> tuple[float, float, float, float]:
            labels = entry.get("labels") or []
            text = str(labels[0]) if labels else ""
            lines = text.splitlines() or [text]
            max_chars = max((len(line) for line in lines), default=1)

            # Approximate VTK text bounds; labels are anchored near lower-left.
            font_size = int(entry.get("base_size", 8))
            if active_spec_index is not None and spec_entries[active_spec_index] is entry:
                font_size = int(entry.get("hover_size", font_size))
            width = max(12.0, float(max_chars) * float(font_size) * 0.62 + 8.0)
            height = max(12.0, float(len(lines)) * float(font_size) * 1.35 + 4.0)
            pad = 6.0
            return (dx - pad, dy - pad, dx + width + pad, dy + height + pad)

        def _annotation_at_position(x: float, y: float) -> int | None:
            new_spec_index: int | None = None
            best_inside_score = float("inf")
            best_anchor_distance_sq = float(16 * 16)
            for idx, entry in enumerate(spec_entries):
                actor = entry.get("actor")
                get_visibility = getattr(actor, "GetVisibility", None)
                if callable(get_visibility) and not get_visibility():
                    continue
                points = entry.get("points") or []
                if not points:
                    continue

                anchor = points[0]
                try:
                    if len(anchor) >= 3:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), float(anchor[2]))
                    else:
                        hover_coordinate.SetValue(float(anchor[0]), float(anchor[1]), 0.0)
                    dx, dy = hover_coordinate.GetComputedDisplayValue(renderer)
                except Exception:
                    continue

                left, bottom, right, top = _estimate_label_bounds(entry, float(dx), float(dy))
                if left <= x <= right and bottom <= y <= top:
                    center_x = 0.5 * (left + right)
                    center_y = 0.5 * (bottom + top)
                    inside_score = (center_x - x) ** 2 + (center_y - y) ** 2
                    if inside_score < best_inside_score:
                        best_inside_score = inside_score
                        new_spec_index = idx
                    continue

                if new_spec_index is not None:
                    continue

                distance_sq = (float(dx) - x) ** 2 + (float(dy) - y) ** 2
                if distance_sq <= best_anchor_distance_sq:
                    best_anchor_distance_sq = distance_sq
                    new_spec_index = idx
            return new_spec_index

        def _on_mouse_move(caller: Any, event: Any) -> None:
            nonlocal active_spec_index
            try:
                x, y = interactor.GetEventPosition()
            except Exception:
                return

            new_spec_index = _annotation_at_position(float(x), float(y))

            if new_spec_index == active_spec_index:
                return

            if active_spec_index is not None:
                prev_entry = spec_entries[active_spec_index]
                _apply_size(active_spec_index, int(prev_entry.get("base_size", 8)))

            if new_spec_index is not None:
                next_entry = spec_entries[new_spec_index]
                _apply_size(new_spec_index, int(next_entry.get("hover_size", 12)))

            active_spec_index = new_spec_index

        def _on_left_button_press(caller: Any, event: Any) -> None:
            try:
                x, y = interactor.GetEventPosition()
            except Exception:
                return

            spec_index = _annotation_at_position(float(x), float(y))
            if spec_index is None:
                return
            config_path = spec_entries[spec_index].get("config_path")
            editor = self._json_editor
            reveal_path = editor.get("reveal_path") if isinstance(editor, dict) else None
            if config_path and callable(reveal_path):
                reveal_path(config_path)

        try:
            tag = interactor.AddObserver("MouseMoveEvent", _on_mouse_move)
        except Exception:
            return

        try:
            click_tag = interactor.AddObserver("LeftButtonPressEvent", _on_left_button_press)
        except Exception:
            click_tag = None

        self._hover_interactor = interactor
        self._hover_observer_tag = tag
        self._annotation_click_observer_tag = click_tag
        hover_installed = True

    def _is_unchanged(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
    ) -> bool:
        if with_particles:
            return False

        payload = {
            "config": config.model_dump(exclude_none=True),
            "config_path": str(resolved_config_path.resolve()),
            "with_particles": with_particles,
        }
        signature = json.dumps(payload, sort_keys=True)
        unchanged = signature == self.last_signature
        self.last_signature = signature
        return unchanged

    def show_or_update(
        self,
        config: SimulationConfig,
        *,
        resolved_config_path: Path,
        with_particles: bool,
        force: bool = False,
    ) -> int:
        try:
            import pyvista as pv  # noqa: F401
        except ImportError:
            print(
                "❌ PyVista is not installed.\n"
                "   Install it with:  pip install sphinxsim[visualization]",
                file=sys.stderr,
            )
            return 1

        if self.plotter is not None and self._using_background_plotter:
            editor_refreshed = self._install_json_editor(
                config,
                config_path=resolved_config_path,
                with_particles=with_particles,
            )
            if not editor_refreshed:
                print("Preview canceled; unapplied editor changes were preserved.")
                return 0
        unchanged = self._is_unchanged(
            config,
            resolved_config_path=resolved_config_path,
            with_particles=with_particles,
        )
        if not force and unchanged:
            print("ℹ️ Preview unchanged; keeping existing window.")
            return 0

        from sphinxsim.visualization.preview import ConfigVisualizer

        visualizer = ConfigVisualizer(
            config,
            PROJECT_ROOT,
            config_path=resolved_config_path,
            off_screen=False,
        )

        ndim = visualizer._spatial_dim()
        vtp_dir: Path | None = None
        latest_particle_vtps: dict[str, Path] = {}

        try:
            # Always rebuild geometry; if VTPs are unavailable the preview
            # falls back to cached bounds and still renders the scene.
            vtp_dir = visualizer._try_build_geometries(ndim, with_particles=with_particles)
            if with_particles:
                latest_particle_vtps = visualizer._discover_latest_particle_vtps(
                    vtp_dir, generation_only=True
                )
            ensure_geometry_available = getattr(
                visualizer, "_ensure_shape_geometry_available", None
            )
            if ensure_geometry_available is not None:
                ensure_geometry_available(
                    vtp_dir,
                    hide_shapes=bool(latest_particle_vtps),
                )

            if sys.platform.startswith("linux"):
                os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
            import pyvista as pv
            pyvistaqt_error: Exception | None = None
            created_plotter = False
            try:
                from pyvistaqt import BackgroundPlotter  # type: ignore[import]
            except Exception as exc:
                BackgroundPlotter = None
                pyvistaqt_error = exc

            if self.plotter is None:
                created_plotter = True
                if BackgroundPlotter is not None:
                    self.plotter = BackgroundPlotter(
                        title="SPHinXsim - Configuration Preview",
                        show=True,
                        shape=(1, 1),
                        border=False,
                        window_size=(1400, 800),
                        # Avoid loading pyvistaqt's bundled PNG icon, whose
                        # invalid ICC metadata causes noisy libpng warnings on
                        # some Windows builds.
                        update_app_icon=False,
                    )
                    self._using_background_plotter = True
                else:
                    self.plotter = pv.Plotter(
                        title="SPHinXsim - Configuration Preview",
                        off_screen=False,
                        shape=(1, 2),
                        border=False,
                        window_size=(1400, 800),
                    )
                    self._using_background_plotter = False
                    detail = f" ({pyvistaqt_error})" if pyvistaqt_error is not None else ""
                    print(
                        "ℹ️ pyvistaqt background mode is unavailable"
                        f"{detail}; persistent preview may become unresponsive while shell waits for input.\n"
                        "   Install dependencies with: pip install pyvistaqt PySide6\n"
                        "   (or use PyQt5 instead of PySide6)",
                        file=sys.stderr,
                    )

            if created_plotter and self._using_background_plotter:
                self._watch_preview_window_close()
                self._install_json_editor(
                    config,
                    config_path=resolved_config_path,
                    with_particles=with_particles,
                )
                from qtpy import QtWidgets
                if isinstance(self.plotter, QtWidgets.QWidget):
                    from sphinxsim.visualization.particle_visibility import (
                        ParticleVisibility, install_particle_menu,
                    )
                    self._particle_visibility = ParticleVisibility(self.plotter)
                    self._particle_menu_filter = install_particle_menu(
                        self.plotter, self._particle_visibility
                    )

            if self._particle_time_display is not None:
                self._particle_time_display.reset()
            self.plotter.clear()
            editor = self._json_editor
            set_preview_elements = (
                editor.get("set_preview_elements")
                if isinstance(editor, dict)
                else None
            )
            if callable(set_preview_elements):
                set_preview_elements({})
            configure_layout = getattr(visualizer, "_configure_layout", None)
            if configure_layout is not None:
                configure_layout(self.plotter)
            draw_sidebar = getattr(visualizer, "_draw_preview_sidebar", None)
            if draw_sidebar is not None:
                draw_sidebar(self.plotter)
            visualizer._populate_plotter(self.plotter, vtp_dir, latest_particle_vtps)
            if self._particle_visibility is not None:
                config_signature = json.dumps(
                    config.model_dump(exclude_none=True), sort_keys=True
                )
                if (
                    resolved_config_path != self._particle_scene_path
                    or config_signature != self._particle_scene_config_signature
                    or with_particles
                ):
                    self._particle_scene_generation += 1
                    self._particle_scene_path = resolved_config_path
                    self._particle_scene_config_signature = config_signature
                self._particle_visibility.set_scene(
                    ("preview", self._particle_scene_generation), config, visualizer.legend_actor
                )
                for body_name in sorted(latest_particle_vtps):
                    actor = self.plotter.renderer.actors.get(f"particle-preview-{body_name}")
                    if actor is not None:
                        self._particle_visibility.add_actor(body_name, actor)
            ensure_shapes_rendered = getattr(visualizer, "_ensure_shapes_rendered", None)
            if ensure_shapes_rendered is not None:
                ensure_shapes_rendered()
            if with_particles:
                self._animation_run_completed = False
                pending_simulation = visualizer.take_particle_simulation()
                if pending_simulation is not None:
                    from sphinxsim.visualization.preview import _body_colour

                    (
                        self._pending_simulation,
                        self._pending_runtime_config_path,
                    ) = pending_simulation
                    self._pending_initialized = False
                    self._live_output_dir = vtp_dir or (
                        PROJECT_ROOT / ".build-temp" / "test_simulation" / "output"
                    )
                    self._live_particle_body_names = visualizer._particle_generation_body_names()
                    self._live_particle_colours = {
                        body_name: _body_colour(body_name, config)
                        for body_name in self._live_particle_body_names
                    }
                    self._animation_output_dir = self._live_output_dir
                    self._animation_particle_body_names = set(self._live_particle_body_names)
                    self._animation_particle_colours = dict(self._live_particle_colours)
                    self._animation_config = config
                    self._animation_config_path = resolved_config_path
                else:
                    self._animation_output_dir = None
                    self._animation_particle_body_names = set()
                    self._animation_particle_colours = {}
                    self._animation_run_completed = False
            if configure_layout is not None:
                configure_layout(self.plotter)
            visualizer._configure_default_view(self.plotter, ndim)
            self.plotter.add_axes()
            self.plotter.show_grid(font_size=10, color=(0.25, 0.25, 0.25), bold=False)

            self._install_annotation_hover(visualizer)
            if callable(set_preview_elements):
                set_preview_elements(visualizer.preview_element_actors)

            if not self._using_background_plotter:
                try:
                    # Non-blocking window path: returns immediately to shell.
                    self.plotter.show(auto_close=False, interactive_update=True)
                except TypeError:
                    # Some mocked plotters do not accept these kwargs.
                    self.plotter.show()
            try:
                self.plotter.render()
            except Exception:
                pass
            if with_particles and self._pending_simulation is not None:
                self.replay_animation(relaxation=True, _automatic=True)
            return 0
        except ImportError as exc:
            self.last_signature = None
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            self.last_signature = None
            print(f"❌ Preview failed: {exc}", file=sys.stderr)
            return 1
