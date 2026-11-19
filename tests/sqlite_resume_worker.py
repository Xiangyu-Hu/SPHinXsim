"""Independent-process worker used to verify durable LangGraph resume."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from sphinxsim.agent import (
    AgentTaskStore,
    DeterministicCalibrationWorkflow,
    MockColumnCollapseCalibrationPlanner,
    open_sqlite_calibration_graph,
)
from sphinxsim.services import CLICommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT
    / "tests"
    / "test_simulation"
    / "test_2d_simulation"
    / "data"
    / "column_collapse.json"
)


def _success(command: str) -> CLICommandResult:
    return CLICommandResult(
        command=(command,),
        returncode=0,
        stdout="ok",
        stderr="",
        duration_seconds=0.01,
    )


class PersistentCountingAdapter:
    """Small deterministic adapter whose solver count survives process exit."""

    cwd = PROJECT_ROOT

    def __init__(self, counter_path: Path) -> None:
        self.counter_path = counter_path

    def generate(self, description, output_path, *, timeout=None) -> CLICommandResult:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(SOURCE_CASE.read_text(encoding="utf-8"), encoding="utf-8")
        return _success("generate")

    def validate(self, config_path, *, timeout=None) -> CLICommandResult:
        json.loads(Path(config_path).read_text(encoding="utf-8"))
        return _success("validate")

    def run(self, config_path, *, output_dir=None, timeout=None) -> CLICommandResult:
        count = 0
        if self.counter_path.exists():
            count = int(self.counter_path.read_text(encoding="utf-8"))
        self.counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.counter_path.write_text(str(count + 1), encoding="utf-8")

        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        angle = math.degrees(
            payload["continuum_bodies"][0]["material"]["friction_angle"]
        )
        final_front = {0.0: 0.45, 10.0: 0.40, 20.0: 0.35, 40.0: 0.28}[
            round(angle, 6)
        ]
        output_path = Path(output_dir) / "output"
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "GranularBody_0000001000.vtp").write_text(
            f'''<?xml version="1.0"?>
<VTKFile type="PolyData" version="1.0" byte_order="LittleEndian">
  <PolyData><FieldData><DataArray Name="TimeValue" format="ascii">0.8</DataArray></FieldData>
    <Piece Name="GranularBody" NumberOfPoints="3"><Points>
      <DataArray Name="Position" NumberOfComponents="3" format="ascii">
        0.10 0.00 0.00 {final_front} 0.02 0.00 0.20 0.05 0.00
      </DataArray>
    </Points></Piece>
  </PolyData>
</VTKFile>
''',
            encoding="utf-8",
        )
        return _success("run")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("start", "resume"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    store = AgentTaskStore(args.root / "store")
    workflow = DeterministicCalibrationWorkflow(
        store,
        MockColumnCollapseCalibrationPlanner(),
        PersistentCountingAdapter(args.root / "solver_calls.txt"),
    )
    config = {"configurable": {"thread_id": "two_process_thread"}}
    with open_sqlite_calibration_graph(
        workflow,
        args.root / "checkpoints.sqlite",
        interrupt_after=["execute_candidate"],
    ) as graph:
        if args.mode == "start":
            graph.invoke(
                {
                    "user_prompt": "Calibrate the runout extension to 0.2 m.",
                    "workflow_id": "two_process_workflow",
                },
                config,
            )
        else:
            graph.invoke(None, config)
        snapshot = graph.get_state(config)

    result = workflow.load("two_process_workflow")
    print(
        json.dumps(
            {
                "mode": args.mode,
                "iterations": len(result.iterations),
                "solver_calls": int(
                    (args.root / "solver_calls.txt").read_text(encoding="utf-8")
                ),
                "next": list(snapshot.next),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
