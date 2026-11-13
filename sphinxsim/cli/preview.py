"""Preview command-layer module for the SPHinXsim CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .common import PROJECT_ROOT, _load_config
from .interactive import _resolve_preview_config_path
from .preview_runtime import _ShellPreviewRuntime as _LegacyShellPreviewRuntime


class _ShellPreviewRuntime(_LegacyShellPreviewRuntime):
    """Compatibility wrapper for the preview runtime in the new module layout."""

    pass


def cmd_preview(args: argparse.Namespace) -> int:
    """Render an interactive geometry/BC preview of a JSON config file."""
    try:
        import pyvista  # noqa: F401
    except ImportError:
        print(
            "❌ PyVista is not installed.\n"
            "   Install it with:  pip install sphinxsim[visualization]",
            file=sys.stderr,
        )
        return 1

    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    resolved_config_path = _resolve_preview_config_path(args.config_file)
    from sphinxsim.visualization.preview import ConfigVisualizer

    off_screen = getattr(args, "off_screen", False)
    screenshot_path = getattr(args, "screenshot", None)
    with_particles = getattr(args, "with_particles", False)
    if screenshot_path:
        off_screen = True

    print(f"🖼  Building configuration preview for: {resolved_config_path}")
    print("   Building C++ geometry; bounds cache fallback will be used if VTP meshes are unavailable.")
    if with_particles:
        print("   Particle generation overlay is enabled (--with-particles).")

    visualizer = ConfigVisualizer(
        config,
        PROJECT_ROOT,
        config_path=resolved_config_path,
        off_screen=off_screen,
    )
    try:
        visualizer.preview(
            screenshot_path=screenshot_path,
            with_particles=with_particles,
        )
        if visualizer.used_cpp_geometry:
            print("✅ Preview used C++ geometry (VTP meshes).")
        else:
            print("ℹ️ Preview used C++ bounds fallback (no VTP meshes produced).")
        if screenshot_path:
            print(f"📸 Screenshot saved to: {screenshot_path}")
    except ImportError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ Preview failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    return 0


__all__ = ["_ShellPreviewRuntime", "cmd_preview"]
