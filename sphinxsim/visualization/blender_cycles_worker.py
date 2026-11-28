"""Standalone Blender Cycles worker script for SPHinXsim Scene Bundles.

Executed exclusively inside Blender's Python interpreter via:
    blender --background --factory-startup --python blender_cycles_worker.py -- <scene.json> [options]

This module intentionally imports ONLY Python standard library modules, NumPy
(bundled with Blender), and Blender's ``bpy`` / ``mathutils`` modules—never
``sphinxsim`` or any C++ pybind11 extension—eliminating Python ABI and native
library collisions across Blender 3.3 LTS through 4.x+.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np


def _set_socket(node: Any, candidate_names: tuple[str, ...], value: Any) -> bool:
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return False
    for name in candidate_names:
        if name in inputs:
            try:
                inputs[name].default_value = value
                return True
            except Exception:
                continue
    return False


def _rgba(rgb: list[float] | tuple[float, ...], alpha: float = 1.0) -> tuple[float, float, float, float]:
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(alpha))


def _configure_cycles_engine(bpy_mod: Any, scene: Any, render_cfg: dict[str, Any]) -> str:
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = int(render_cfg.get("width", 1600))
    scene.render.resolution_y = int(render_cfg.get("height", 900))
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(render_cfg.get("transparent_bg", False))
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    cycles = scene.cycles
    cycles.samples = int(render_cfg.get("samples", 64))
    if hasattr(cycles, "use_adaptive_sampling"):
        cycles.use_adaptive_sampling = True
    if hasattr(cycles, "adaptive_threshold"):
        cycles.adaptive_threshold = 0.01

    use_denoising = bool(render_cfg.get("use_denoising", True))
    if hasattr(cycles, "use_denoising"):
        cycles.use_denoising = use_denoising
    if use_denoising and hasattr(cycles, "denoiser"):
        for candidate in ("OPENIMAGEDENOISE", "OPTIX"):
            try:
                cycles.denoiser = candidate
                break
            except Exception:
                continue

    if hasattr(cycles, "max_bounces"):
        cycles.max_bounces = 8
    if hasattr(cycles, "transmission_bounces"):
        cycles.transmission_bounces = 8
    if hasattr(cycles, "transparent_max_bounces"):
        cycles.transparent_max_bounces = 12

    # Prefer AgX (Blender 4.0+) or Filmic (Blender 3.x) for highlight roll-off.
    view_settings = getattr(scene, "view_settings", None)
    if view_settings is not None:
        for view_transform in ("AgX", "Filmic", "Standard"):
            try:
                view_settings.view_transform = view_transform
                break
            except Exception:
                continue

    # Probe GPU backends safely; fall back to CPU when unavailable.
    selected_backend = "CPU"
    cycles_.device = "CPU" if (cycles_ := cycles) else "CPU"
    try:
        prefs = bpy_mod.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = backend
                if hasattr(prefs, "get_devices"):
                    prefs.get_devices()
                elif hasattr(prefs, "refresh_devices"):
                    prefs.refresh_devices()
                non_cpu = [d for d in getattr(prefs, "devices", []) if getattr(d, "type", "CPU") != "CPU"]
                if non_cpu:
                    for dev in prefs.devices:
                        dev.use = getattr(dev, "type", "CPU") != "CPU"
                    cycles.device = "GPU"
                    selected_backend = backend
                    break
            except Exception:
                continue
    except Exception:
        cycles.device = "CPU"

    return selected_backend


def _clear_default_scene(bpy_mod: Any) -> None:
    for obj in list(bpy_mod.data.objects):
        bpy_mod.data.objects.remove(obj, do_unlink=True)
    for block_collection in (
        getattr(bpy_mod.data, "meshes", []),
        getattr(bpy_mod.data, "materials", []),
        getattr(bpy_mod.data, "lights", []),
        getattr(bpy_mod.data, "cameras", []),
        getattr(bpy_mod.data, "pointclouds", []),
        getattr(bpy_mod.data, "node_groups", []),
    ):
        for block in list(block_collection):
            try:
                block_collection.remove(block)
            except Exception:
                pass


def _setup_world_and_lights(
    bpy_mod: Any,
    mathutils_mod: Any,
    scene: Any,
    world_cfg: dict[str, Any],
) -> None:
    world = scene.world
    if world is None:
        world = bpy_mod.data.worlds.new("SPHinXsimWorld")
        scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    out_node = nt.nodes.new("ShaderNodeOutputWorld")
    mix_shader = nt.nodes.new("ShaderNodeMixShader")
    light_path = nt.nodes.new("ShaderNodeLightPath")
    cam_bg = nt.nodes.new("ShaderNodeBackground")
    env_bg = nt.nodes.new("ShaderNodeBackground")

    bg_col = world_cfg.get("background_color", [0.09, 0.10, 0.12])
    sky_horizon = world_cfg.get("sky_horizon", [0.72, 0.75, 0.80])
    cam_bg.inputs["Color"].default_value = _rgba(bg_col, 1.0)
    cam_bg.inputs["Strength"].default_value = 1.0
    env_bg.inputs["Color"].default_value = _rgba(sky_horizon, 1.0)
    env_bg.inputs["Strength"].default_value = float(world_cfg.get("env_strength", 0.95))

    nt.links.new(light_path.outputs["Is Camera Ray"], mix_shader.inputs[0])
    nt.links.new(env_bg.outputs["Background"], mix_shader.inputs[1])
    nt.links.new(cam_bg.outputs["Background"], mix_shader.inputs[2])
    nt.links.new(mix_shader.outputs["Shader"], out_node.inputs["Surface"])

    for light_spec in world_cfg.get("lights", []):
        ldata = bpy_mod.data.lights.new(light_spec["name"], "SUN")
        ldata.energy = float(light_spec.get("energy", 2.5))
        ldata.color = tuple(float(c) for c in light_spec.get("color", [1.0, 1.0, 1.0])[:3])
        if hasattr(ldata, "angle"):
            ldata.angle = math.radians(float(light_spec.get("angle_deg", 8.0)))
        lobj = bpy_mod.data.objects.new(light_spec["name"], ldata)
        scene.collection.objects.link(lobj)
        direction = mathutils_mod.Vector(tuple(float(v) for v in light_spec.get("direction", [0.0, 0.0, -1.0])))
        if direction.length > 1e-8:
            direction.normalize()
            lobj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    ground = world_cfg.get("ground_plane") or {}
    if ground.get("enabled"):
        size = float(ground.get("size", 10.0))
        half = 0.5 * size
        axis = ground.get("axis", "Y")
        cx, cy, cz = (float(v) for v in ground.get("center", [0.0, 0.0, 0.0]))
        if axis == "Z":
            verts = [
                (cx - half, cy - half, cz),
                (cx + half, cy - half, cz),
                (cx + half, cy + half, cz),
                (cx - half, cy + half, cz),
            ]
        else:
            verts = [
                (cx - half, cy, cz - half),
                (cx + half, cy, cz - half),
                (cx + half, cy, cz + half),
                (cx - half, cy, cz + half),
            ]
        gmesh = bpy_mod.data.meshes.new("SPHinXsimGroundMesh")
        gmesh.from_pydata(verts, [], [(0, 1, 2, 3)])
        gmesh.update()
        gobj = bpy_mod.data.objects.new("SPHinXsimGround", gmesh)
        scene.collection.objects.link(gobj)
        if hasattr(gobj, "is_shadow_catcher"):
            gobj.is_shadow_catcher = bool(ground.get("shadow_catcher", True))


def _setup_camera(
    bpy_mod: Any,
    mathutils_mod: Any,
    scene: Any,
    cam_cfg: dict[str, Any],
) -> Any:
    cam_data = bpy_mod.data.cameras.new("SPHinXsimCamera")
    cam_type = str(cam_cfg.get("type", "PERSP")).upper()
    cam_data.type = "ORTHO" if cam_type == "ORTHO" else "PERSP"
    if hasattr(cam_data, "sensor_fit"):
        cam_data.sensor_fit = str(cam_cfg.get("sensor_fit", "VERTICAL"))
    if cam_data.type == "ORTHO":
        cam_data.ortho_scale = float(cam_cfg.get("ortho_scale", 2.0))
    else:
        if hasattr(cam_data, "lens_unit"):
            cam_data.lens_unit = "FOV"
        cam_data.angle = math.radians(float(cam_cfg.get("fov_y_degrees", 30.0)))

    cam_data.clip_start = float(cam_cfg.get("clip_start", 1e-4))
    cam_data.clip_end = float(cam_cfg.get("clip_end", 1e4))

    cam_obj = bpy_mod.data.objects.new("SPHinXsimCamera", cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.matrix_world = mathutils_mod.Matrix(cam_cfg["matrix_world"])
    scene.camera = cam_obj
    return cam_obj


def _build_mesh_material(bpy_mod: Any, name: str, mat_spec: dict[str, Any]) -> Any:
    mat = bpy_mod.data.materials.new(name=f"Mat_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    kind = mat_spec.get("kind", "matte_solid")
    rgb = mat_spec.get("color", [0.5, 0.5, 0.5])
    alpha = float(mat_spec.get("alpha", 1.0))
    roughness = float(mat_spec.get("roughness", 0.35))
    ior = float(mat_spec.get("ior", 1.45))

    if kind == "acrylic_wall" or (alpha < 0.999 and kind != "wireframe"):
        # Blend transparent pass-through with glossy Principled BSDF so internal particles
        # remain brightly lit without casting dark opaque shadows into the tank.
        mix_node = nt.nodes.new("ShaderNodeMixShader")
        transp = nt.nodes.new("ShaderNodeBsdfTransparent")
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        transp.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        _set_socket(bsdf, ("Base Color",), _rgba(rgb, 1.0))
        _set_socket(bsdf, ("Roughness",), roughness)
        _set_socket(bsdf, ("IOR",), ior)
        _set_socket(bsdf, ("Specular IOR Level", "Specular"), 0.5)
        mix_node.inputs[0].default_value = alpha
        nt.links.new(transp.outputs[0], mix_node.inputs[1])
        nt.links.new(bsdf.outputs[0], mix_node.inputs[2])
        nt.links.new(mix_node.outputs[0], out_node.inputs["Surface"])
    else:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        _set_socket(bsdf, ("Base Color",), _rgba(rgb, 1.0))
        _set_socket(bsdf, ("Roughness",), roughness)
        _set_socket(bsdf, ("IOR",), ior)
        nt.links.new(bsdf.outputs[0], out_node.inputs["Surface"])

    return mat


def _build_particle_material(bpy_mod: Any, p_spec: dict[str, Any]) -> Any:
    body = p_spec["body"]
    mat = bpy_mod.data.materials.new(name=f"ParticleMat_{body}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")

    scalar_spec = p_spec.get("scalar")
    is_fluid_surface = bool(p_spec.get("fluid_surface", False))
    role = p_spec.get("role", "fluid")
    base_rgb = p_spec.get("base_color", [0.20, 0.53, 0.85])

    if scalar_spec is not None:
        attr_node = nt.nodes.new("ShaderNodeAttribute")
        attr_node.attribute_name = "sphinx_scalar"
        if hasattr(attr_node, "attribute_type"):
            attr_node.attribute_type = "GEOMETRY"

        map_range = nt.nodes.new("ShaderNodeMapRange")
        map_range.clamp = True
        map_range.inputs["From Min"].default_value = float(scalar_spec["vmin"])
        map_range.inputs["From Max"].default_value = float(scalar_spec["vmax"])
        map_range.inputs["To Min"].default_value = 0.0
        map_range.inputs["To Max"].default_value = 1.0

        ramp_node = nt.nodes.new("ShaderNodeValToRGB")
        ramp = ramp_node.color_ramp
        stops = scalar_spec.get("stops") or [[0.0, [0.0, 0.0, 1.0]], [1.0, [1.0, 0.0, 0.0]]]
        while len(ramp.elements) > 1:
            ramp.elements.remove(ramp.elements[-1])
        ramp.elements[0].position = float(stops[0][0])
        ramp.elements[0].color = _rgba(stops[0][1], 1.0)
        for pos, col in stops[1:]:
            elem = ramp.elements.new(float(pos))
            elem.color = _rgba(col, 1.0)

        _set_socket(bsdf, ("Roughness",), 0.12 if is_fluid_surface else 0.24)
        _set_socket(bsdf, ("IOR",), 1.333 if role == "fluid" else 1.45)
        _set_socket(bsdf, ("Coat Weight", "Clearcoat"), 0.25 if role == "fluid" else 0.0)
        nt.links.new(attr_node.outputs["Fac"], map_range.inputs["Value"])
        nt.links.new(map_range.outputs["Result"], ramp_node.inputs["Fac"])
        nt.links.new(ramp_node.outputs["Color"], bsdf.inputs["Base Color"])
    elif is_fluid_surface and role == "fluid":
        # Dielectric refractive water shader with subtle tint.
        tinted = [0.55 + 0.45 * float(c) for c in base_rgb]
        _set_socket(bsdf, ("Base Color",), _rgba(tinted, 1.0))
        _set_socket(bsdf, ("Roughness",), 0.03)
        _set_socket(bsdf, ("IOR",), 1.333)
        _set_socket(bsdf, ("Transmission Weight", "Transmission"), 0.96)
    else:
        _set_socket(bsdf, ("Base Color",), _rgba(base_rgb, 1.0))
        _set_socket(bsdf, ("Roughness",), 0.20 if role == "fluid" else 0.42)
        _set_socket(bsdf, ("IOR",), 1.333 if role == "fluid" else 1.48)
        _set_socket(bsdf, ("Coat Weight", "Clearcoat"), 0.20 if role == "fluid" else 0.0)

    nt.links.new(bsdf.outputs[0], out_node.inputs["Surface"])
    return mat


def _attach_geonodes_modifier(
    bpy_mod: Any,
    obj: Any,
    material: Any,
    *,
    radius: float,
    particle_spacing: float,
    mesh_to_points: bool,
    fluid_surface: bool,
) -> None:
    mod = obj.modifiers.new(name="SPHinXsimParticlesGN", type="NODES")
    ng = bpy_mod.data.node_groups.new(name=f"GN_{obj.name}", type="GeometryNodeTree")
    mod.node_group = ng

    # Declare Geometry input/output sockets across Blender 4.x (interface) and 3.x (inputs/outputs).
    if hasattr(ng, "interface"):
        ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    else:
        ng.inputs.new("NodeSocketGeometry", "Geometry")
        ng.outputs.new("NodeSocketGeometry", "Geometry")

    nodes = ng.nodes
    links = ng.links
    n_in = nodes.new("NodeGroupInput")
    n_out = nodes.new("NodeGroupOutput")

    current_socket = n_in.outputs[0]
    if mesh_to_points:
        m2p = nodes.new("GeometryNodeMeshToPoints")
        _set_socket(m2p, ("Radius",), float(radius))
        links.new(current_socket, m2p.inputs["Mesh"])
        current_socket = m2p.outputs["Points"]

    if fluid_surface:
        p2v = nodes.new("GeometryNodePointsToVolume")
        if hasattr(p2v, "resolution_mode"):
            try:
                p2v.resolution_mode = "VOXEL_SIZE"
            except Exception:
                pass
        voxel_size = max(float(particle_spacing) * 0.65, 1e-5)
        _set_socket(p2v, ("Voxel Size",), voxel_size)
        _set_socket(p2v, ("Radius",), float(particle_spacing) * 1.55)
        _set_socket(p2v, ("Density",), 1.0)
        links.new(current_socket, p2v.inputs["Points"])

        v2m = nodes.new("GeometryNodeVolumeToMesh")
        _set_socket(v2m, ("Threshold",), 0.30)
        _set_socket(v2m, ("Adaptivity",), 0.0)
        links.new(p2v.outputs["Volume"], v2m.inputs["Volume"])

        smooth = nodes.new("GeometryNodeSetShadeSmooth")
        _set_socket(smooth, ("Shade Smooth",), True)
        links.new(v2m.outputs["Mesh"], smooth.inputs["Geometry"])
        current_socket = smooth.outputs["Geometry"]

    set_mat = nodes.new("GeometryNodeSetMaterial")
    _set_socket(set_mat, ("Material",), material)
    links.new(current_socket, set_mat.inputs["Geometry"])
    links.new(set_mat.outputs["Geometry"], n_out.inputs[0])


def _load_static_meshes(
    bpy_mod: Any,
    scene: Any,
    bundle_dir: Path,
    meshes_cfg: list[dict[str, Any]],
    scene_diagonal: float,
) -> list[Any]:
    created: list[Any] = []
    for m_spec in meshes_cfg:
        pos = np.fromfile(bundle_dir / m_spec["positions"]["file"], dtype="<f4").reshape(-1, 3)
        tri = np.fromfile(bundle_dir / m_spec["indices"]["file"], dtype="<u4").reshape(-1, 3)
        if len(pos) == 0 or len(tri) == 0:
            continue

        mesh = bpy_mod.data.meshes.new(f"Mesh_{m_spec['id']}")
        mesh.from_pydata(pos.tolist(), [], tri.tolist())
        mesh.update()

        obj = bpy_mod.data.objects.new(f"Shape_{m_spec['id']}", mesh)
        scene.collection.objects.link(obj)
        mat = _build_mesh_material(bpy_mod, m_spec["id"], m_spec.get("material", {}))
        mesh.materials.append(mat)

        if m_spec.get("wireframe"):
            wmod = obj.modifiers.new(name="DomainWireframe", type="WIREFRAME")
            wmod.thickness = max(scene_diagonal * 0.0025, 1e-5)

        created.append(obj)
    return created


def _create_frame_particles(
    bpy_mod: Any,
    scene: Any,
    bundle_dir: Path,
    particles_cfg: list[dict[str, Any]],
) -> list[tuple[Any, Any, Any]]:
    created: list[tuple[Any, Any, Any]] = []
    has_pointcloud_add = False
    pc_collection = getattr(bpy_mod.data, "pointclouds", None)
    if pc_collection is not None:
        probe = pc_collection.new("_probe_pc")
        has_pointcloud_add = hasattr(getattr(probe, "points", None), "add")
        pc_collection.remove(probe)

    for p_spec in particles_cfg:
        body = p_spec["body"]
        pos = np.fromfile(bundle_dir / p_spec["positions"]["file"], dtype="<f4").reshape(-1, 3)
        count = len(pos)
        if count == 0:
            continue

        radius = float(p_spec["radius"])
        spacing = float(p_spec.get("particle_spacing", radius * 2.0))
        fluid_surface = bool(p_spec.get("fluid_surface", False))
        scalar_spec = p_spec.get("scalar")
        scalars: np.ndarray | None = None
        if scalar_spec is not None and "values" in scalar_spec:
            scalars = np.fromfile(bundle_dir / scalar_spec["values"]["file"], dtype="<f4")

        mat = _build_particle_material(bpy_mod, p_spec)

        # If fluid_surface AND scalar coloring are both active, use Mesh + SampleNearest or PointCloud;
        # PointCloud works natively for spheres and for uncolored fluid_surface!
        if has_pointcloud_add and not (fluid_surface and scalars is not None):
            pc_data = bpy_mod.data.pointclouds.new(f"PointCloud_{body}")
            pc_data.points.add(count)
            pc_data.points.foreach_set("co", pos.ravel())
            radii = np.full(count, radius, dtype=np.float32)
            pc_data.points.foreach_set("radius", radii)
            if scalars is not None and len(scalars) == count:
                attr = pc_data.attributes.new(name="sphinx_scalar", type="FLOAT", domain="POINT")
                attr.data.foreach_set("value", scalars)
            pc_data.materials.append(mat)
            obj = bpy_mod.data.objects.new(f"Particles_{body}", pc_data)
            scene.collection.objects.link(obj)
            if fluid_surface:
                _attach_geonodes_modifier(
                    bpy_mod,
                    obj,
                    mat,
                    radius=radius,
                    particle_spacing=spacing,
                    mesh_to_points=False,
                    fluid_surface=True,
                )
            created.append((obj, pc_data, mat))
        else:
            # Blender 3.3–3.6 LTS compatible fallback (and scalar-preserving path): vertex mesh + MeshToPoints.
            vmesh = bpy_mod.data.meshes.new(f"PointVerts_{body}")
            vmesh.vertices.add(count)
            vmesh.vertices.foreach_set("co", pos.ravel())
            vmesh.update()
            if scalars is not None and len(scalars) == count:
                attr = vmesh.attributes.new(name="sphinx_scalar", type="FLOAT", domain="POINT")
                attr.data.foreach_set("value", scalars)
            vmesh.materials.append(mat)
            obj = bpy_mod.data.objects.new(f"Particles_{body}", vmesh)
            scene.collection.objects.link(obj)
            _attach_geonodes_modifier(
                bpy_mod,
                obj,
                mat,
                radius=radius,
                particle_spacing=spacing,
                mesh_to_points=True,
                fluid_surface=fluid_surface and scalars is None,
            )
            created.append((obj, vmesh, mat))

    return created


def _remove_frame_particles(bpy_mod: Any, entries: list[tuple[Any, Any, Any]]) -> None:
    for obj, data_block, mat in entries:
        try:
            bpy_mod.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass
        for collection_name in ("pointclouds", "meshes"):
            coll = getattr(bpy_mod.data, collection_name, None)
            if coll is not None:
                try:
                    coll.remove(data_block)
                except Exception:
                    pass
        try:
            bpy_mod.data.materials.remove(mat)
        except Exception:
            pass


def build_and_render_scene(
    scene_json_path: str | Path,
    *,
    output_image: str | Path | None = None,
    sequence_dir: str | Path | None = None,
    save_blend: str | Path | None = None,
    no_render: bool = False,
    bpy_module: Any = None,
    mathutils_module: Any = None,
) -> dict[str, Any]:
    """Load ``scene.json``, populate Blender scene, and render requested frame(s)."""
    if bpy_module is None:
        import bpy as bpy_module  # type: ignore[import-not-found]
    if mathutils_module is None:
        import mathutils as mathutils_module  # type: ignore[import-not-found]

    scene_path = Path(scene_json_path).resolve()
    bundle_dir = scene_path.parent
    spec = json.loads(scene_path.read_text(encoding="utf-8"))

    scene = bpy_module.context.scene
    _clear_default_scene(bpy_module)
    backend = _configure_cycles_engine(bpy_module, scene, spec.get("render", {}))
    _setup_world_and_lights(bpy_module, mathutils_module, scene, spec.get("world", {}))
    _setup_camera(bpy_module, mathutils_module, scene, spec["camera"])

    diag = float(spec.get("scene_bounds", {}).get("diagonal", 1.0))
    _load_static_meshes(bpy_module, scene, bundle_dir, spec.get("meshes", []), diag)

    frames = spec.get("frames") or [{"index": 0, "particles": []}]
    rendered_files: list[str] = []
    seq_root = Path(sequence_dir).resolve() if sequence_dir is not None else None
    if seq_root is not None:
        seq_root.mkdir(parents=True, exist_ok=True)

    active_particles: list[tuple[Any, Any, Any]] = []
    for idx, frame_spec in enumerate(frames):
        if active_particles:
            _remove_frame_particles(bpy_module, active_particles)
        active_particles = _create_frame_particles(
            bpy_module,
            scene,
            bundle_dir,
            frame_spec.get("particles", []),
        )

        target_img: Path | None = None
        if seq_root is not None:
            rel_name = frame_spec.get("output_image") or f"frame_{idx:04d}.png"
            target_img = seq_root / rel_name
        elif output_image is not None and idx == len(frames) - 1:
            target_img = Path(output_image).resolve()
        elif frame_spec.get("output_image"):
            target_img = bundle_dir / frame_spec["output_image"]

        if target_img is not None and not no_render:
            target_img.parent.mkdir(parents=True, exist_ok=True)
            scene.render.filepath = str(target_img)
            bpy_module.ops.render.render(write_still=True)
            rendered_files.append(str(target_img))

    saved_blend_path: str | None = None
    if save_blend is not None:
        blend_dest = Path(save_blend).resolve()
        blend_dest.parent.mkdir(parents=True, exist_ok=True)
        bpy_module.ops.wm.save_as_mainfile(filepath=str(blend_dest))
        saved_blend_path = str(blend_dest)

    return {
        "backend": backend,
        "rendered_files": rendered_files,
        "saved_blend": saved_blend_path,
        "frames_processed": len(frames),
    }


def _parse_worker_argv(argv: list[str]) -> argparse.Namespace:
    if "--" in argv:
        user_args = argv[argv.index("--") + 1 :]
    else:
        user_args = argv[1:]
    parser = argparse.ArgumentParser(description="SPHinXsim Blender Cycles worker")
    parser.add_argument("scene_json", help="Path to exported scene.json")
    parser.add_argument("--output", default=None, help="Output PNG file path for single-frame render")
    parser.add_argument("--sequence-dir", default=None, help="Output directory for multi-frame render sequence")
    parser.add_argument("--save-blend", default=None, help="Optional destination .blend project file")
    parser.add_argument("--no-render", action="store_true", help="Build .blend scene without invoking Cycles ray-tracing")
    return parser.parse_args(user_args)


def main(argv: list[str] | None = None) -> int:
    args = _parse_worker_argv(argv if argv is not None else sys.argv)
    summary = build_and_render_scene(
        args.scene_json,
        output_image=args.output,
        sequence_dir=args.sequence_dir,
        save_blend=args.save_blend,
        no_render=args.no_render,
    )
    print("SPHINXSIM_CYCLES_RESULT:" + json.dumps(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
