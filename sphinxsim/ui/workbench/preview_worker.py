"""Isolated native geometry generation; the GUI thread only renders completed output."""
from pathlib import Path
import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("draft")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--particles", action="store_true")
    args = parser.parse_args()
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.visualization.preview import ConfigVisualizer
    from sphinxsim.ui.workbench.document import rebase_asset_paths
    payload = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    config = SimulationConfig.model_validate(rebase_asset_paths(payload, Path(args.source).parent))
    visualizer = ConfigVisualizer(config, Path.cwd(), config_path=Path(args.draft), output_root=Path(args.output))
    geometry = visualizer._try_build_geometries(visualizer._spatial_dim(), with_particles=args.particles)
    if geometry is None and not visualizer.used_cpp_bounds:
        raise RuntimeError("C++ geometry generation failed; see the preview log.")
    Path(args.output, "ready.json").write_text(json.dumps({"bounds": visualizer._shape_bounds_cache}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
