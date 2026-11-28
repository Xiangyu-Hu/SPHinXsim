"""Blender executable discovery, subprocess orchestration, and high-level Cycles renderers."""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from sphinxsim.runtime.export_vtp import hash_file
from sphinxsim.visualization.cycles_export import (
    CyclesSceneState,
    export_cycles_scene_bundle,
    export_job_manifest_cycles_bundle,
)
from sphinxsim.visualization.cycles_overlay import composite_scientific_overlay


WORKER_SCRIPT = Path(__file__).with_name("blender_cycles_worker.py").resolve()


def parse_resolution(spec: str | tuple[int, int] | None, default: tuple[int, int] = (1600, 900)) -> tuple[int, int]:
    """Parse a ``WIDTHxHEIGHT`` resolution string or tuple."""
    if spec is None:
        return default
    if isinstance(spec, tuple) and len(spec) == 2:
        w, h = int(spec[0]), int(spec[1])
    else:
        raw = str(spec).strip().lower()
        if "x" not in raw:
            raise ValueError(f"Invalid resolution {spec!r}; expected format WIDTHxHEIGHT (e.g. 1600x900)")
        left, right = raw.split("x", 1)
        try:
            w, h = int(left.strip()), int(right.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid resolution {spec!r}; width and height must be integers") from exc
    if not (64 <= w <= 16384 and 64 <= h <= 16384):
        raise ValueError(f"Resolution {w}x{h} out of supported range (64x64 .. 16384x16384)")
    return w, h


def find_blender_executable(explicit_path: str | Path | None = None) -> Path | None:
    """Locate a local ``blender`` binary via explicit path, env var, PATH, or standard install roots."""
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
        found = shutil.which(str(explicit_path))
        if found:
            return Path(found).resolve()
        raise FileNotFoundError(f"Specified Blender executable was not found or is not executable: {explicit_path}")

    env_exec = os.getenv("SPHINXSIM_BLENDER_EXECUTABLE", "").strip()
    if env_exec:
        env_path = Path(env_exec).expanduser()
        if env_path.is_file() and os.access(env_path, os.X_OK):
            return env_path.resolve()
        found_env = shutil.which(env_exec)
        if found_env:
            return Path(found_env).resolve()
        raise FileNotFoundError(
            f"SPHINXSIM_BLENDER_EXECUTABLE={env_exec!r} does not point to an executable Blender binary"
        )

    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path).resolve()

    platform_candidates = [
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/snap/bin/blender",
        *sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"), reverse=True),
    ]
    for item in platform_candidates:
        p = Path(item)
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
    return None


def blender_render_command(
    scene_file: str | Path,
    *,
    blender_executable: str | Path = "blender",
    output_image: str | Path | None = None,
    sequence_dir: str | Path | None = None,
    save_blend: str | Path | None = None,
    no_render: bool = False,
) -> list[str]:
    """Return the exact CLI command list to render an exported ``scene.json`` via Blender."""
    cmd = [
        str(blender_executable),
        "--background",
        "--factory-startup",
        "--python",
        str(WORKER_SCRIPT),
        "--",
        str(Path(scene_file).resolve()),
    ]
    if output_image is not None:
        cmd.extend(["--output", str(Path(output_image).resolve())])
    if sequence_dir is not None:
        cmd.extend(["--sequence-dir", str(Path(sequence_dir).resolve())])
    if save_blend is not None:
        cmd.extend(["--save-blend", str(Path(save_blend).resolve())])
    if no_render:
        cmd.append("--no-render")
    return cmd


def run_blender_cycles(
    scene_file: str | Path,
    *,
    output_image: str | Path | None = None,
    sequence_dir: str | Path | None = None,
    save_blend: str | Path | None = None,
    blender_executable: str | Path | None = None,
    apply_overlay: bool = True,
    timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    """Invoke Blender headless subprocess on ``scene_file`` and apply optional 2D scientific overlays."""
    resolved_scene = Path(scene_file).resolve()
    blender_bin = find_blender_executable(blender_executable)
    if blender_bin is None:
        manual_cmd = " ".join(
            blender_render_command(
                "<bundle>/scene.json",
                blender_executable="blender",
                output_image=output_image or "render.png",
            )
        )
        raise RuntimeError(
            "Blender executable was not found on PATH (and SPHINXSIM_BLENDER_EXECUTABLE is not set). "
            "Install Blender 3.6+ / 4.x, set SPHINXSIM_BLENDER_EXECUTABLE=/path/to/blender, "
            f"or pass --save-bundle DIR to export a portable Cycles bundle and render later via:\n  {manual_cmd}"
        )

    no_render = output_image is None and sequence_dir is None
    cmd = blender_render_command(
        resolved_scene,
        blender_executable=blender_bin,
        output_image=output_image,
        sequence_dir=sequence_dir,
        save_blend=save_blend,
        no_render=no_render,
    )
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if proc.returncode != 0:
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        tail = "\n".join(combined.splitlines()[-25:])
        raise RuntimeError(f"Blender Cycles worker exited with code {proc.returncode}:\n{tail}")

    worker_result: dict[str, Any] = {}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("SPHINXSIM_CYCLES_RESULT:"):
            try:
                worker_result = json.loads(line.split("SPHINXSIM_CYCLES_RESULT:", 1)[1])
            except json.JSONDecodeError:
                pass
            break

    rendered_files = [Path(p) for p in worker_result.get("rendered_files", [])]
    if output_image is not None and not rendered_files:
        out_p = Path(output_image).resolve()
        if out_p.is_file():
            rendered_files = [out_p]
        else:
            raise RuntimeError(f"Blender Cycles completed without writing expected image: {out_p}")

    if apply_overlay and rendered_files:
        spec = json.loads(resolved_scene.read_text(encoding="utf-8"))
        frames = spec.get("frames") or []
        if len(rendered_files) == 1 and frames:
            composite_scientific_overlay(rendered_files[0], frames[-1].get("overlay") or {})
        else:
            for idx, img_file in enumerate(rendered_files):
                if idx < len(frames) and img_file.is_file():
                    composite_scientific_overlay(img_file, frames[idx].get("overlay") or {})

    return {
        "blender_executable": str(blender_bin),
        "backend": worker_result.get("backend", "CPU"),
        "rendered_files": [str(p) for p in rendered_files],
        "saved_blend": worker_result.get("saved_blend"),
    }


def render_results_with_cycles(
    config_path: str | Path,
    output_dir: str | Path,
    screenshot_path: str | Path | None = None,
    *,
    sequence_dir: str | Path | None = None,
    save_bundle: str | Path | None = None,
    save_blend: str | Path | None = None,
    color_by: str | None = None,
    frame: str = "latest",
    samples: int = 64,
    resolution: str | tuple[int, int] | None = None,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
    apply_overlay: bool = True,
    blender_executable: str | Path | None = None,
    scene_state: CyclesSceneState | None = None,
) -> dict[str, Any]:
    """Export and/or render SPHinXsim solver results using Blender Cycles."""
    from sphinxsim.config.migrations import migrate_legacy_configuration
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.results import AnalysisError, _resolve_run_data_dir
    from sphinxsim.runtime.physics import check_config
    from sphinxsim.visualization.preview import ConfigVisualizer

    resolved_config = Path(config_path).resolve()
    resolved_output = Path(output_dir).resolve()
    data_dir, _ = _resolve_run_data_dir(resolved_output)
    if not data_dir.is_dir():
        raise AnalysisError(f"Simulation output directory does not exist: {data_dir}")

    raw_payload = migrate_legacy_configuration(json.loads(resolved_config.read_text(encoding="utf-8")))
    normalized, _ = check_config(raw_payload)
    config = SimulationConfig.model_validate(normalized)
    spatial_dim = ConfigVisualizer(config, resolved_config.parent, config_path=resolved_config, off_screen=True)._spatial_dim()
    width, height = parse_resolution(resolution)

    def _run_with_bundle(b_dir: Path) -> dict[str, Any]:
        export_info = export_cycles_scene_bundle(
            config,
            b_dir,
            spatial_dim=spatial_dim,
            config_path=resolved_config,
            data_dir=data_dir,
            frame=frame,
            color_by=color_by,
            scene_state=scene_state,
            width=width,
            height=height,
            samples=samples,
            fluid_surface=fluid_surface,
            transparent_bg=transparent_bg,
        )
        if frame.strip().lower() != "all" and not export_info["bodies_rendered"]:
            raise AnalysisError(f"No particle VTP snapshots found in {data_dir}")

        needs_blender = screenshot_path is not None or sequence_dir is not None or save_blend is not None
        out_img = screenshot_path
        seq_out = sequence_dir
        if frame.strip().lower() == "all" and seq_out is None and out_img is not None:
            # When --frame all is combined with --screenshot dir_or_file, write sequence frames into a directory.
            cand = Path(out_img)
            seq_out = cand.parent / cand.stem if cand.suffix.lower() == ".png" else cand
            out_img = None

        render_info: dict[str, Any] = {}
        if needs_blender:
            render_info = run_blender_cycles(
                export_info["scene_file"],
                output_image=out_img,
                sequence_dir=seq_out,
                save_blend=save_blend,
                blender_executable=blender_executable,
                apply_overlay=apply_overlay,
            )

        result: dict[str, Any] = {
            "renderer": "cycles",
            "bodies_rendered": export_info["bodies_rendered"],
            "color_by": color_by,
            "coloured_bodies": export_info["coloured_bodies"],
            "frame": frame,
            "frames_exported": export_info["frames_exported"],
            "samples": int(samples),
            "resolution": f"{width}x{height}",
            "fluid_surface": bool(fluid_surface),
        }
        if save_bundle is not None:
            result["bundle_dir"] = export_info["bundle_dir"]
            result["scene_file"] = export_info["scene_file"]
            result["render_command"] = " ".join(
                blender_render_command(
                    export_info["scene_file"],
                    output_image=screenshot_path or "render.png",
                )
            )
        if render_info.get("rendered_files"):
            primary = Path(render_info["rendered_files"][-1])
            result["file"] = str(primary)
            result["rendered_files"] = render_info["rendered_files"]
            result["bytes"] = primary.stat().st_size
            result["sha256"] = hash_file(primary)
            result["backend"] = render_info.get("backend", "CPU")
        if render_info.get("saved_blend"):
            result["saved_blend"] = render_info["saved_blend"]
        return result

    if save_bundle is not None:
        return _run_with_bundle(Path(save_bundle).resolve())
    with tempfile.TemporaryDirectory(prefix="sphinxsim_cycles_") as tmp:
        return _run_with_bundle(Path(tmp))


def render_preview_with_cycles(
    config: Any,
    resolved_config_path: str | Path,
    *,
    project_root: str | Path,
    screenshot_path: str | Path | None = None,
    save_bundle: str | Path | None = None,
    save_blend: str | Path | None = None,
    with_particles: bool = False,
    vtp_dir: str | Path | None = None,
    particle_vtps: dict[str, Path] | None = None,
    shape_bounds: dict[str, Any] | None = None,
    scene_state: CyclesSceneState | None = None,
    color_by: str | None = None,
    samples: int = 64,
    resolution: str | tuple[int, int] | None = None,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
    apply_overlay: bool = True,
    blender_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Build C++ geometries (if needed) and export/render a pre-run or live preview via Cycles."""
    from sphinxsim.visualization.preview import ConfigVisualizer

    cfg_path = Path(resolved_config_path).resolve()
    proj_root = Path(project_root).resolve()
    visualizer = ConfigVisualizer(config, proj_root, config_path=cfg_path, off_screen=True)
    ndim = visualizer._spatial_dim()

    active_vtp_dir = Path(vtp_dir).resolve() if vtp_dir is not None else None
    active_particles = dict(particle_vtps) if particle_vtps is not None else None
    active_bounds = shape_bounds

    if active_vtp_dir is None and active_bounds is None and active_particles is None:
        try:
            active_vtp_dir = visualizer._try_build_geometries(ndim, with_particles=with_particles)
            active_bounds = visualizer._shape_bounds_cache
            if with_particles:
                active_particles = visualizer._discover_latest_particle_vtps(active_vtp_dir)
        finally:
            visualizer._discard_particle_simulation()

    width, height = parse_resolution(resolution)
    shape_dirs = [active_vtp_dir] if active_vtp_dir is not None else []

    def _run_with_bundle(b_dir: Path) -> dict[str, Any]:
        export_info = export_cycles_scene_bundle(
            config,
            b_dir,
            spatial_dim=ndim,
            config_path=cfg_path,
            shape_dirs=shape_dirs,
            shape_bounds=active_bounds,
            explicit_particle_vtps=active_particles,
            color_by=color_by,
            scene_state=scene_state,
            width=width,
            height=height,
            samples=samples,
            fluid_surface=fluid_surface,
            transparent_bg=transparent_bg,
            show_domain=not bool(active_particles),
        )
        needs_blender = screenshot_path is not None or save_blend is not None
        render_info: dict[str, Any] = {}
        if needs_blender:
            render_info = run_blender_cycles(
                export_info["scene_file"],
                output_image=screenshot_path,
                save_blend=save_blend,
                blender_executable=blender_executable,
                apply_overlay=apply_overlay,
            )
        result: dict[str, Any] = {
            "renderer": "cycles",
            "used_cpp_geometry": active_vtp_dir is not None,
            "bodies_rendered": export_info["bodies_rendered"],
            "meshes_exported": export_info["meshes_exported"],
            "samples": int(samples),
            "resolution": f"{width}x{height}",
        }
        if save_bundle is not None:
            result["bundle_dir"] = export_info["bundle_dir"]
            result["scene_file"] = export_info["scene_file"]
            result["render_command"] = " ".join(
                blender_render_command(
                    export_info["scene_file"],
                    output_image=screenshot_path or "preview_cycles.png",
                )
            )
        if render_info.get("rendered_files"):
            primary = Path(render_info["rendered_files"][-1])
            result["file"] = str(primary)
            result["bytes"] = primary.stat().st_size
            result["sha256"] = hash_file(primary)
            result["backend"] = render_info.get("backend", "CPU")
        if render_info.get("saved_blend"):
            result["saved_blend"] = render_info["saved_blend"]
        return result

    if save_bundle is not None:
        return _run_with_bundle(Path(save_bundle).resolve())
    with tempfile.TemporaryDirectory(prefix="sphinxsim_cycles_preview_") as tmp:
        return _run_with_bundle(Path(tmp))


def render_job_manifest_with_cycles(
    result_dir: str | Path,
    manifest: dict[str, Any],
    config: Any,
    output_image: str | Path | None = None,
    *,
    save_bundle: str | Path | None = None,
    color_by: str | None = None,
    samples: int = 64,
    resolution: str | tuple[int, int] | None = None,
    fluid_surface: bool = False,
    transparent_bg: bool = False,
    apply_overlay: bool = True,
    blender_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Export and/or render an immutable published MCP job manifest via Blender Cycles."""
    width, height = parse_resolution(resolution)

    def _run_with_bundle(b_dir: Path) -> dict[str, Any]:
        export_info = export_job_manifest_cycles_bundle(
            result_dir,
            manifest,
            config,
            b_dir,
            color_by=color_by,
            width=width,
            height=height,
            samples=samples,
            fluid_surface=fluid_surface,
            transparent_bg=transparent_bg,
        )
        render_info: dict[str, Any] = {}
        if output_image is not None:
            render_info = run_blender_cycles(
                export_info["scene_file"],
                output_image=output_image,
                blender_executable=blender_executable,
                apply_overlay=apply_overlay,
            )
        res: dict[str, Any] = {
            "renderer": "cycles",
            "bodies_rendered": export_info["bodies_rendered"],
            "meshes_exported": export_info["meshes_exported"],
        }
        if save_bundle is not None:
            res["bundle_dir"] = export_info["bundle_dir"]
            res["scene_file"] = export_info["scene_file"]
        if render_info.get("rendered_files"):
            primary = Path(render_info["rendered_files"][-1])
            res["file"] = str(primary)
            res["bytes"] = primary.stat().st_size
            res["sha256"] = hash_file(primary)
        return res

    if save_bundle is not None:
        return _run_with_bundle(Path(save_bundle).resolve())
    with tempfile.TemporaryDirectory(prefix="sphinxsim_cycles_job_") as tmp:
        return _run_with_bundle(Path(tmp))
