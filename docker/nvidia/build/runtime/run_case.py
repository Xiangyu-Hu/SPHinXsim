"""Container entry point with explicit case/output paths; no LLM required."""
import argparse
import json
from pathlib import Path
import tempfile

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.bindings.loader import load_sphinxsys_core_nd


def main():
    parser = argparse.ArgumentParser(description="Run a JSON SPHinXsim case on a visible NVIDIA GPU.")
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--end-time", type=float, help="Override physical end time for smoke tests")
    args = parser.parse_args()
    case = args.case.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty; use a separate directory for each run.")
    data = json.loads(case.read_text())
    if args.end_time is not None:
        if args.end_time <= 0:
            parser.error("--end-time must be positive")
        data["solver_parameters"]["end_time"] = args.end_time
        data["solver_parameters"]["output_interval"] = min(
            data["solver_parameters"].get("output_interval", 0.1), args.end_time / 2
        )
    config = SimulationConfig.model_validate(data)
    dimension = config._infer_spatial_dim()
    if dimension not in (2, 3):
        parser.error("Cannot determine case dimension")
    # Keep the temporary config beside the original to preserve relative assets.
    # Config directories must therefore be mounted writable (documented).
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix=".sphinxsim-", dir=case.parent) as stream:
        stream.write(config.model_dump_json(exclude_none=True, indent=2))
        stream.flush()
        core = load_sphinxsys_core_nd(dimension)
        sim = core.SPHSimulation(stream.name)
        output.mkdir(parents=True, exist_ok=True)
        (output / "config.json").write_text(config.model_dump_json(exclude_none=True, indent=2))
        print(f"Running {dimension}D case {case}; output: {output}", flush=True)
        sim.resetOutputRoot(str(output))
        sim.buildGeometries()
        sim.generateParticles()
        sim.buildSimulation()
        sim.initializeSimulation()
        sim.run()
    print("SPHinXsim case completed", flush=True)


if __name__ == "__main__":
    main()
