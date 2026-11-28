"""Offline CI tests for SPHinXsim Blender Cycles scene export, worker, overlay, CLI, GUI, and MCP."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

if sys.platform not in ("win32", "darwin") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

from sphinxsim.cli import main as cli_main
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.operations import Operations
from sphinxsim.runtime.export_vtp import hash_file
from sphinxsim.visualization.blender_cycles_worker import build_and_render_scene
from sphinxsim.visualization.cycles_export import (
    CyclesSceneState,
    build_cycles_camera,
    export_cycles_scene_bundle,
    read_vtp_bundle_data,
    resolve_scalar_mapping,
)
from sphinxsim.visualization.cycles_overlay import composite_scientific_overlay
from sphinxsim.visualization.cycles_runner import (
    find_blender_executable,
    parse_resolution,
    render_results_with_cycles,
)


def _sample_2d_config_dict() -> dict[str, Any]:
    from sphinxsim.llm import get_llm

    return get_llm("mock").generate("2D water dam break").model_dump(mode="json", exclude_none=True)


def _write_ascii_particle_vtp(
    path: Path,
    *,
    time_value: float = 0.25,
    dim: int = 2,
    points_components: int = 3,
    vel_ref: float = 4.0,
    press_ref: float = 1500.0,
) -> None:
    if points_components == 2:
        pts_txt = "0.1 0.1  0.3 0.1  0.1 0.4  0.4 0.6"
    elif dim == 2:
        pts_txt = "0.1 0.1 0.0  0.3 0.1 0.0  0.1 0.4 0.0  0.4 0.6 0.0"
    else:
        pts_txt = "0.1 0.1 0.1  0.3 0.1 0.2  0.1 0.4 0.3  0.4 0.6 0.4"
    if dim == 2:
        vel_txt = "1.0 0.0  0.0 2.0  3.0 4.0  0.0 0.0"
    else:
        vel_txt = "1.0 0.0 0.0  0.0 2.0 0.0  0.0 3.0 4.0  0.0 0.0 0.0"
    press_txt = "-500.0 0.0 750.0 1200.0"
    xml = f"""<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <FieldData>
      <DataArray type="Float64" Name="TimeValue" NumberOfTuples="1" format="ascii">{time_value}</DataArray>
      <DataArray type="Float64" Name="ScalingReference_Velocity" NumberOfTuples="1" format="ascii">{vel_ref}</DataArray>
      <DataArray type="Float64" Name="ScalingReference_Pressure" NumberOfTuples="1" format="ascii">{press_ref}</DataArray>
    </FieldData>
    <Piece Name="WaterBody" NumberOfPoints="4" NumberOfVerts="0" NumberOfLines="0" NumberOfStrips="0" NumberOfPolys="0">
      <Points>
        <DataArray type="Float32" Name="Position" NumberOfComponents="{points_components}" format="ascii">{pts_txt}</DataArray>
      </Points>
      <PointData>
        <DataArray type="Float32" Name="Velocity" NumberOfComponents="{dim}" format="ascii">{vel_txt}</DataArray>
        <DataArray type="Float32" Name="Pressure" NumberOfComponents="1" format="ascii">{press_txt}</DataArray>
      </PointData>
    </Piece>
  </PolyData>
</VTKFile>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")


def _write_ascii_shape_vtp(path: Path) -> None:
    xml = """<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData>
    <Piece NumberOfPoints="5" NumberOfPolys="2">
      <Points>
        <DataArray type="Float32" Name="Points" NumberOfComponents="3" format="ascii">
          0 0 0  2 0 0  2 1 0  0 1 0  1 1.5 0
        </DataArray>
      </Points>
      <Polys>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3  3 2 4</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4 7</DataArray>
      </Polys>
    </Piece>
  </PolyData>
</VTKFile>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")


# ---------------------------------------------------------------------------
# Lightweight Fake bpy + mathutils Harness for Offline Worker Verification
# ---------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self, name: str, default_value: Any = None) -> None:
        self.name = name
        self.default_value = default_value


class _FakeSocketMap:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._items = [(_FakeSocket(n)) for n in names]
        self._by_name = {s.name: s for s in self._items}

    def __contains__(self, key: str) -> bool:
        return key in self._by_name

    def __getitem__(self, key: str | int) -> _FakeSocket:
        if isinstance(key, int):
            return self._items[key]
        if key not in self._by_name:
            sock = _FakeSocket(key)
            self._items.append(sock)
            self._by_name[key] = sock
        return self._by_name[key]

    def new(self, sock_type: str, name: str) -> _FakeSocket:
        sock = _FakeSocket(name)
        self._items.append(sock)
        self._by_name[name] = sock
        return sock


class _FakeRampElement:
    def __init__(self, position: float, color: tuple[float, float, float, float] = (0, 0, 0, 1)) -> None:
        self.position = position
        self.color = color


class _FakeRampElements(list):
    def new(self, position: float) -> _FakeRampElement:
        el = _FakeRampElement(position)
        self.append(el)
        return el


class _FakeNode:
    def __init__(self, node_type: str) -> None:
        self.bl_idname = node_type
        self.attribute_name = ""
        self.attribute_type = "GEOMETRY"
        self.clamp = False
        self.resolution_mode = "VOXEL_AMOUNT"
        self.inputs = _FakeSocketMap(
            (
                "Fac",
                "Surface",
                "Color",
                "Strength",
                "Base Color",
                "Roughness",
                "IOR",
                "Specular IOR Level",
                "Transmission Weight",
                "Coat Weight",
                "Value",
                "From Min",
                "From Max",
                "To Min",
                "To Max",
                "Geometry",
                "Mesh",
                "Points",
                "Volume",
                "Radius",
                "Voxel Size",
                "Density",
                "Threshold",
                "Adaptivity",
                "Shade Smooth",
                "Material",
            )
        )
        self.outputs = _FakeSocketMap(
            ("Fac", "Color", "Result", "Shader", "Background", "Is Camera Ray", "Geometry", "Points", "Volume", "Mesh")
        )
        self.color_ramp = SimpleNamespace(
            elements=_FakeRampElements([_FakeRampElement(0.0), _FakeRampElement(1.0)])
        )


class _FakeLinks(list):
    def new(self, src: _FakeSocket, dst: _FakeSocket) -> tuple[_FakeSocket, _FakeSocket]:
        pair = (src, dst)
        self.append(pair)
        return pair


class _FakeNodes(list):
    def new(self, node_type: str) -> _FakeNode:
        n = _FakeNode(node_type)
        self.append(n)
        return n


class _FakeNodeTree:
    def __init__(self, name: str = "Tree") -> None:
        self.name = name
        self.nodes = _FakeNodes()
        self.links = _FakeLinks()
        self.interface = SimpleNamespace(new_socket=lambda **_kw: None)


class _FakeCollection(list):
    def __init__(self, factory) -> None:
        super().__init__()
        self._factory = factory

    def new(self, *args, **kwargs):
        item = self._factory(*args, **kwargs)
        self.append(item)
        return item

    def remove(self, item, **_kwargs) -> None:
        if item in self:
            super().remove(item)


class _FakeAttrArray:
    def __init__(self) -> None:
        self.values: list[float] = []

    def foreach_set(self, _prop: str, seq: Any) -> None:
        self.values = [float(x) for x in seq]


class _FakeAttributes(dict):
    def new(self, name: str, type: str, domain: str) -> Any:
        attr = SimpleNamespace(name=name, type=type, domain=domain, data=_FakeAttrArray())
        self[name] = attr
        return attr


class _FakePoints:
    def __init__(self, allow_add: bool = True) -> None:
        self.count = 0
        self.co: list[float] = []
        self.radius: list[float] = []
        if allow_add:
            self.add = self._add  # type: ignore[attr-defined]

    def _add(self, n: int) -> None:
        self.count += int(n)

    def foreach_set(self, prop: str, seq: Any) -> None:
        vals = [float(x) for x in seq]
        if prop == "co":
            self.co = vals
        elif prop == "radius":
            self.radius = vals


def _make_fake_blender(*, allow_pointcloud_add: bool = True):
    def _new_mesh(name: str):
        verts = _FakePoints(allow_add=True)
        return SimpleNamespace(
            name=name,
            vertices=verts,
            polygons=[],
            materials=[],
            attributes=_FakeAttributes(),
            from_pydata=lambda v, _e, f: (setattr(verts, "co", [c for pt in v for c in pt]), None)[1],
            update=lambda: None,
        )

    def _new_pc(name: str):
        return SimpleNamespace(
            name=name,
            points=_FakePoints(allow_add=allow_pointcloud_add),
            materials=[],
            attributes=_FakeAttributes(),
        )

    def _new_mat(name: str):
        return SimpleNamespace(name=name, use_nodes=False, node_tree=_FakeNodeTree(name))

    def _new_obj(name: str, object_data: Any = None):
        mods = _FakeCollection(lambda name, type: SimpleNamespace(name=name, type=type, node_group=None, thickness=0.0))
        return SimpleNamespace(
            name=name,
            data=object_data,
            modifiers=mods,
            matrix_world=None,
            rotation_euler=(0.0, 0.0, 0.0),
            is_shadow_catcher=False,
        )

    scene = SimpleNamespace(
        render=SimpleNamespace(
            engine="BLENDER_EEVEE",
            resolution_x=800,
            resolution_y=600,
            resolution_percentage=100,
            film_transparent=False,
            filepath="",
            image_settings=SimpleNamespace(file_format="PNG", color_mode="RGBA"),
        ),
        cycles=SimpleNamespace(
            samples=16,
            use_adaptive_sampling=False,
            adaptive_threshold=0.05,
            use_denoising=False,
            denoiser="OPENIMAGEDENOISE",
            max_bounces=4,
            transmission_bounces=4,
            transparent_max_bounces=4,
            device="CPU",
        ),
        view_settings=SimpleNamespace(view_transform="Standard"),
        world=SimpleNamespace(use_nodes=False, node_tree=_FakeNodeTree("World")),
        collection=SimpleNamespace(objects=SimpleNamespace(link=lambda _o: None)),
        camera=None,
    )

    def _write_dummy_png(write_still: bool = False) -> None:
        if write_still and scene.render.filepath:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
            _ = app
            img = QtGui.QImage(
                scene.render.resolution_x,
                scene.render.resolution_y,
                QtGui.QImage.Format.Format_ARGB32,
            )
            img.fill(QtGui.QColor(24, 28, 34, 0 if scene.render.film_transparent else 255))
            out = Path(scene.render.filepath)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(out), "PNG")

    def _save_blend(filepath: str) -> None:
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"BLENDER-v402-MOCK")

    bpy_mod = SimpleNamespace(
        context=SimpleNamespace(
            scene=scene,
            preferences=SimpleNamespace(addons={}),
        ),
        data=SimpleNamespace(
            objects=_FakeCollection(_new_obj),
            meshes=_FakeCollection(_new_mesh),
            pointclouds=_FakeCollection(_new_pc),
            materials=_FakeCollection(_new_mat),
            lights=_FakeCollection(lambda name, type: SimpleNamespace(name=name, type=type, energy=1.0, color=(1, 1, 1), angle=0.1)),
            cameras=_FakeCollection(lambda name: SimpleNamespace(name=name, type="PERSP", sensor_fit="AUTO", ortho_scale=1.0, lens_unit="MILLIMETERS", angle=0.5, clip_start=0.1, clip_end=100.0)),
            worlds=_FakeCollection(lambda name: SimpleNamespace(name=name, use_nodes=False, node_tree=_FakeNodeTree(name))),
            node_groups=_FakeCollection(lambda name, type: _FakeNodeTree(name)),
        ),
        ops=SimpleNamespace(
            render=SimpleNamespace(render=_write_dummy_png),
            wm=SimpleNamespace(save_as_mainfile=_save_blend),
        ),
    )

    class _FakeVector:
        def __init__(self, vals):
            self.v = np.asarray(vals, dtype=float)

        @property
        def length(self) -> float:
            return float(np.linalg.norm(self.v))

        def normalize(self) -> None:
            n = self.length
            if n > 0:
                self.v = self.v / n

        def to_track_quat(self, *_args):
            return SimpleNamespace(to_euler=lambda: (0.1, 0.2, 0.3))

    mathutils_mod = SimpleNamespace(
        Vector=_FakeVector,
        Matrix=lambda rows: [list(r) for r in rows],
    )
    return bpy_mod, mathutils_mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pure_python_vtp_reader_2d_3d_and_incomplete(tmp_path: Path) -> None:
    p_vtp = tmp_path / "WaterBody_0001.vtp"
    _write_ascii_particle_vtp(
        p_vtp,
        time_value=0.35,
        dim=2,
        points_components=2,
        vel_ref=3.5,
        press_ref=900.0,
    )
    for prefer_vtk in (False, True):
        data = read_vtp_bundle_data(p_vtp, prefer_vtk=prefer_vtk)
        assert data.points.shape == (4, 3)
        assert data.points.dtype == np.float32
        assert np.allclose(data.points[:, 2], 0.0)
        assert data.time_value == pytest.approx(0.35)
        assert data.point_data["Velocity"].shape == (4, 2)
        assert data.point_data["Pressure"].shape == (4,)
        assert float(data.field_data["ScalingReference_Velocity"].ravel()[0]) == pytest.approx(3.5)

    s_vtp = tmp_path / "ShapeWallBoundary.vtp"
    _write_ascii_shape_vtp(s_vtp)
    s_data = read_vtp_bundle_data(s_vtp, prefer_vtk=False)
    assert s_data.points.shape == (5, 3)
    # 1 quad (2 tris) + 1 triangle = 3 triangles
    assert s_data.triangles.shape == (3, 3)

    incomplete = tmp_path / "partial.vtp"
    incomplete.write_text("<VTKFile><PolyData>", encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        read_vtp_bundle_data(incomplete, prefer_vtk=False)


def test_cycles_camera_ortho_persp_and_live_vtk_roundtrip() -> None:
    cam_2d = build_cycles_camera(2, [0.0, 0.0, 0.0], [2.0, 1.0, 0.0], width=1600, height=900)
    assert cam_2d["type"] == "ORTHO"
    assert cam_2d["sensor_fit"] == "VERTICAL"
    assert cam_2d["ortho_scale"] > 1.0
    rot_2d = np.asarray(cam_2d["matrix_world"], dtype=float)[:3, :3]
    assert np.allclose(rot_2d.T @ rot_2d, np.eye(3), atol=1e-6)
    assert float(np.linalg.det(rot_2d)) == pytest.approx(1.0, abs=1e-6)

    live_state = {
        "position": ((3.0, 2.0, 4.0), (0.5, 0.2, 0.0), (0.0, 1.0, 0.0)),
        "parallel_projection": False,
        "parallel_scale": 1.25,
        "view_angle": 42.0,
    }
    cam_live = build_cycles_camera(3, [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], camera_state=live_state)
    assert cam_live["type"] == "PERSP"
    assert cam_live["fov_y_degrees"] == pytest.approx(42.0)
    assert cam_live["ortho_scale"] == pytest.approx(2.5)
    mat = np.asarray(cam_live["matrix_world"], dtype=float)
    # Local -Z column in Blender camera matrix must align with normalized (focal - pos)
    expected_fwd = np.array([0.5 - 3.0, 0.2 - 2.0, 0.0 - 4.0])
    expected_fwd /= np.linalg.norm(expected_fwd)
    blender_minus_z = -mat[:3, 2]
    assert np.allclose(blender_minus_z, expected_fwd, atol=1e-6)


def test_scalar_vector_mapping_and_clip_filtering_parity(tmp_path: Path) -> None:
    cfg = SimulationConfig.model_validate(_sample_2d_config_dict())
    out_dir = tmp_path / "sim_out" / "output"
    _write_ascii_shape_vtp(out_dir / "ShapeWallBoundary.vtp")
    _write_ascii_particle_vtp(out_dir / "WaterBody_0001.vtp", time_value=0.1, vel_ref=4.0, press_ref=1500.0)
    _write_ascii_particle_vtp(out_dir / "WaterBody_0002.vtp", time_value=0.2, vel_ref=4.0, press_ref=1500.0)

    vtp_data = read_vtp_bundle_data(out_dir / "WaterBody_0002.vtp", prefer_vtk=False)
    # Vector field with ScalingReference_Velocity=4.0 -> [0.0, 4.0] and 2-stop Blue-to-red
    vel_map = resolve_scalar_mapping("WaterBody", "Velocity", vtp_data.point_data, vtp_data.field_data)
    assert vel_map is not None
    assert vel_map["is_vector_magnitude"] is True
    assert (vel_map["vmin"], vel_map["vmax"]) == pytest.approx((0.0, 4.0))
    assert len(vel_map["stops"]) == 2
    assert vel_map["array"][2] == pytest.approx(5.0)  # hypot(3, 4)

    # Scalar field with ScalingReference_Pressure=1500.0 -> [-1500.0, 1500.0] and 3-stop diverging
    p_map = resolve_scalar_mapping("WaterBody", "Pressure", vtp_data.point_data, vtp_data.field_data)
    assert p_map is not None
    assert p_map["is_vector_magnitude"] is False
    assert (p_map["vmin"], p_map["vmax"]) == pytest.approx((-1500.0, 1500.0))
    assert len(p_map["stops"]) == 3

    # Clip plane keeping x <= 0.2 -> keeps 2 of the 4 particles (x=0.1, 0.1; drops x=0.3, 0.4)
    state = CyclesSceneState(
        clipped_bodies={"WaterBody"},
        clip_origins={"WaterBody": (0.2, 0.0, 0.0)},
        clip_normals={"WaterBody": (1.0, 0.0, 0.0)},
        palettes={"WaterBody": "Viridis"},
        rescale_ranges={("WaterBody", "Velocity"): (0.5, 6.0)},
    )
    bundle_dir = tmp_path / "bundle"
    info = export_cycles_scene_bundle(
        cfg,
        bundle_dir,
        spatial_dim=2,
        data_dir=out_dir,
        frame="latest",
        color_by="Velocity",
        scene_state=state,
    )
    assert info["bodies_rendered"] == ["WaterBody"]
    assert info["coloured_bodies"] == ["WaterBody"]
    scene_json = json.loads(Path(info["scene_file"]).read_text(encoding="utf-8"))
    frame0 = scene_json["frames"][0]
    assert frame0["time"] == pytest.approx(0.2)
    p_entry = frame0["particles"][0]
    assert p_entry["count"] == 2
    assert p_entry["scalar"]["palette"] == "Viridis"
    assert (p_entry["scalar"]["vmin"], p_entry["scalar"]["vmax"]) == pytest.approx((0.5, 6.0))
    pos_bin = bundle_dir / p_entry["positions"]["file"]
    assert hash_file(pos_bin) == p_entry["positions"]["sha256"]


def test_blender_cycles_worker_mock_bpy_pointcloud_fluid_surface_and_sequence(tmp_path: Path) -> None:
    cfg = SimulationConfig.model_validate(_sample_2d_config_dict())
    out_dir = tmp_path / "sim_out" / "output"
    _write_ascii_shape_vtp(out_dir / "ShapeWallBoundary.vtp")
    _write_ascii_particle_vtp(out_dir / "WaterBody_0001.vtp", time_value=0.1)
    _write_ascii_particle_vtp(out_dir / "WaterBody_0002.vtp", time_value=0.2)

    bundle_dir = tmp_path / "seq_bundle"
    export_cycles_scene_bundle(
        cfg,
        bundle_dir,
        spatial_dim=2,
        data_dir=out_dir,
        frame="all",
        color_by="Velocity",
        samples=32,
        width=320,
        height=200,
    )

    # 1. Blender 4.x PointCloud path + multi-frame sequence + .blend export
    fake_bpy, fake_mathutils = _make_fake_blender(allow_pointcloud_add=True)
    seq_renders = tmp_path / "renders"
    blend_out = tmp_path / "project.blend"
    res = build_and_render_scene(
        bundle_dir / "scene.json",
        sequence_dir=seq_renders,
        save_blend=blend_out,
        bpy_module=fake_bpy,
        mathutils_module=fake_mathutils,
    )
    assert res["frames_processed"] == 2
    assert len(res["rendered_files"]) == 2
    assert (seq_renders / "frame_0000.png").is_file()
    assert (seq_renders / "frame_0001.png").is_file()
    assert blend_out.is_file()
    assert fake_bpy.context.scene.render.engine == "CYCLES"
    assert fake_bpy.context.scene.cycles.samples == 32
    assert len(fake_bpy.data.pointclouds) == 1
    assert "sphinx_scalar" in fake_bpy.data.pointclouds[0].attributes

    # 2. Fluid-surface Geometry Nodes + Blender 3.6 LTS MeshToPoints fallback
    fs_bundle = tmp_path / "fs_bundle"
    export_cycles_scene_bundle(
        cfg,
        fs_bundle,
        spatial_dim=2,
        data_dir=out_dir,
        frame="latest",
        fluid_surface=True,
        transparent_bg=True,
        width=320,
        height=200,
    )
    fake_bpy_36, fake_mathutils_36 = _make_fake_blender(allow_pointcloud_add=False)
    still_png = tmp_path / "fluid_surface.png"
    res_fs = build_and_render_scene(
        fs_bundle / "scene.json",
        output_image=still_png,
        bpy_module=fake_bpy_36,
        mathutils_module=fake_mathutils_36,
    )
    assert still_png.is_file()
    assert fake_bpy_36.context.scene.render.film_transparent is True
    gn_types = [
        node.bl_idname
        for ng in fake_bpy_36.data.node_groups
        for node in ng.nodes
    ]
    assert "GeometryNodeMeshToPoints" in gn_types
    assert "GeometryNodePointsToVolume" in gn_types
    assert "GeometryNodeVolumeToMesh" in gn_types
    assert res_fs["rendered_files"] == [str(still_png)]


def test_scientific_qt_overlay_compositor_headless(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
    _ = app

    raw_png = tmp_path / "raw.png"
    base_img = QtGui.QImage(640, 360, QtGui.QImage.Format.Format_ARGB32)
    base_img.fill(QtGui.QColor(22, 25, 30, 255))
    assert base_img.save(str(raw_png), "PNG")
    before_bytes = raw_png.read_bytes()

    overlay_spec = {
        "time": 0.375,
        "is_relaxation": False,
        "legend": [{"label": "WaterBody Particles", "color": [0.20, 0.53, 0.85]}],
        "colorbars": [
            {
                "title": "WaterBody: Velocity magnitude",
                "vmin": 0.0,
                "vmax": 4.5,
                "palette": "Blue to red",
                "stops": [[0.0, [0.23, 0.30, 0.75]], [1.0, [0.71, 0.02, 0.15]]],
            }
        ],
    }
    out_png = composite_scientific_overlay(raw_png, overlay_spec, output_path=tmp_path / "composited.png")
    assert out_png.is_file()
    assert out_png.read_bytes() != before_bytes
    loaded = QtGui.QImage(str(out_png))
    assert (loaded.width(), loaded.height()) == (640, 360)


def test_cli_mcp_and_fake_blender_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg_file = ws / "case.json"
    cfg_file.write_text(json.dumps(_sample_2d_config_dict(), indent=2), encoding="utf-8")
    out_dir = ws / "run_01" / "output"
    _write_ascii_shape_vtp(out_dir / "ShapeWallBoundary.vtp")
    _write_ascii_particle_vtp(out_dir / "WaterBody_0001.vtp", time_value=0.25)

    # 1. CLI --save-bundle works completely offline with no Blender installed
    bundle_out = ws / "exported_bundle"
    rc = cli_main(
        [
            "results",
            str(cfg_file),
            "--output-dir",
            str(ws / "run_01"),
            "--save-bundle",
            str(bundle_out),
            "--color-by",
            "Velocity",
        ]
    )
    assert rc == 0
    assert (bundle_out / "scene.json").is_file()
    out_text = capsys.readouterr().out
    assert "Cycles scene bundle saved to" in out_text

    # 2. Structured MCP Operations results_summary with save_bundle
    ops = Operations(ws)
    mcp_res = ops.call(
        "results_summary",
        {
            "config_file": "case.json",
            "output_dir": "run_01",
            "save_bundle": "mcp_bundle",
            "color_by": "Pressure",
            "samples": 48,
        },
    )
    assert mcp_res["rendered"]["renderer"] == "cycles"
    assert (ws / "mcp_bundle" / "scene.json").is_file()

    # 3. Actionable error when --renderer cycles --screenshot is requested with no Blender binary
    from unittest.mock import patch

    with patch("sphinxsim.visualization.cycles_runner.find_blender_executable", return_value=None):
        rc_missing = cli_main(
            [
                "results",
                str(cfg_file),
                "--output-dir",
                str(ws / "run_01"),
                "--renderer",
                "cycles",
                "--screenshot",
                str(ws / "missing.png"),
            ]
        )
    assert rc_missing == 1
    err_text = capsys.readouterr().err
    assert "SPHINXSIM_BLENDER_EXECUTABLE" in err_text
    assert "--save-bundle" in err_text

    # 4. End-to-end subprocess orchestration with a cross-platform mock Blender launcher
    fake_blender_py = tmp_path / "fake_blender_worker_entry.py"
    repo_root = Path(__file__).resolve().parents[1]
    fake_blender_py.write_text(
        f"""#!{sys.executable}
import sys
sys.path.insert(0, {str(repo_root)!r})
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from test_cycles_visualization import _make_fake_blender
from sphinxsim.visualization.blender_cycles_worker import _parse_worker_argv, build_and_render_scene
import json

args = _parse_worker_argv(sys.argv)
bpy_mod, mathutils_mod = _make_fake_blender(allow_pointcloud_add=True)
summary = build_and_render_scene(
    args.scene_json,
    output_image=args.output,
    sequence_dir=args.sequence_dir,
    save_blend=args.save_blend,
    no_render=args.no_render,
    bpy_module=bpy_mod,
    mathutils_module=mathutils_mod,
)
print("SPHINXSIM_CYCLES_RESULT:" + json.dumps(summary))
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        fake_blender = tmp_path / "fake_blender.cmd"
        fake_blender.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake_blender_py}" %*\r\n',
            encoding="utf-8",
        )
    else:
        fake_blender = fake_blender_py
        fake_blender.chmod(fake_blender.stat().st_mode | stat.S_IXUSR)

    rendered_png = ws / "cycles_final.png"
    saved_blend = ws / "cycles_final.blend"
    rc_ok = cli_main(
        [
            "results",
            str(cfg_file),
            "--output-dir",
            str(ws / "run_01"),
            "--renderer",
            "cycles",
            "--screenshot",
            str(rendered_png),
            "--save-blend",
            str(saved_blend),
            "--color-by",
            "Velocity",
            "--resolution",
            "400x240",
            "--blender",
            str(fake_blender),
        ]
    )
    assert rc_ok == 0
    assert rendered_png.is_file()
    assert saved_blend.is_file()
    final_img = QtGui.QImage(str(rendered_png))
    assert (final_img.width(), final_img.height()) == (400, 240)


def test_preview_dock_render_menu_preserves_simulation_lifecycle_menu(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])
    _ = app

    from sphinxsim.visualization.preview_runtime import _ShellPreviewRuntime

    cfg_path = tmp_path / "case.json"
    cfg_dict = _sample_2d_config_dict()
    cfg_path.write_text(json.dumps(cfg_dict, indent=2), encoding="utf-8")
    cfg = SimulationConfig.model_validate(cfg_dict)

    main_win = QtWidgets.QMainWindow()
    runtime = _ShellPreviewRuntime()
    runtime.plotter = SimpleNamespace(
        app_window=main_win,
        clear=lambda: None,
        render=lambda: None,
        camera_position=((1.0, 0.5, 4.0), (1.0, 0.5, 0.0), (0.0, 1.0, 0.0)),
        camera=SimpleNamespace(parallel_projection=True, parallel_scale=0.75, view_angle=30.0),
    )
    runtime._using_background_plotter = True
    runtime._last_config = cfg
    runtime._last_config_path = cfg_path
    runtime._last_shape_bounds = {"WaterBody": ([0.0, 0.0], [0.5, 0.8]), "WallBoundary": ([-0.1, -0.1], [2.1, 1.1])}

    runtime._install_json_editor(
        cfg,
        config_path=cfg_path,
        with_particles=False,
        simulation_actions=runtime._simulation_properties_actions(),
        render_actions=runtime._render_menu_actions(),
    )

    sim_menu = main_win.findChild(QtWidgets.QMenu, "sphinxsim-simulation-properties-menu")
    render_menu = main_win.findChild(QtWidgets.QMenu, "sphinxsim-render-menu")
    assert sim_menu is not None
    assert len(sim_menu.actions()) == 4
    assert render_menu is not None
    assert len(render_menu.actions()) == 3

    bundle_dir = tmp_path / "live_preview_bundle"
    exported = runtime.export_or_render_cycles(save_bundle=bundle_dir)
    assert Path(exported["scene_file"]).is_file()
    scene_spec = json.loads(Path(exported["scene_file"]).read_text(encoding="utf-8"))
    assert scene_spec["camera"]["ortho_scale"] == pytest.approx(1.5)
    main_win.close()


def test_parse_resolution_validation() -> None:
    assert parse_resolution(None) == (1600, 900)
    assert parse_resolution("1920x1080") == (1920, 1080)
    with pytest.raises(ValueError):
        parse_resolution("bad")
    with pytest.raises(ValueError):
        parse_resolution("10x10")


@pytest.mark.skipif(find_blender_executable() is None, reason="Blender binary not installed locally")
def test_live_blender_cycles_smoke(tmp_path: Path) -> None:
    cfg_file = tmp_path / "case.json"
    cfg_file.write_text(json.dumps(_sample_2d_config_dict(), indent=2), encoding="utf-8")
    out_dir = tmp_path / "run_01" / "output"
    _write_ascii_shape_vtp(out_dir / "ShapeWallBoundary.vtp")
    _write_ascii_particle_vtp(out_dir / "WaterBody_0001.vtp", time_value=0.1)
    out_png = tmp_path / "live_cycles.png"
    res = render_results_with_cycles(
        cfg_file,
        tmp_path / "run_01",
        out_png,
        samples=4,
        resolution="320x200",
        color_by="Velocity",
    )
    assert out_png.is_file()
    assert res["bytes"] > 0
