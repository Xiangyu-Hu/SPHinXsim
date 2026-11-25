"""Process-boundary acceptance tests for SQLite graph checkpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER = PROJECT_ROOT / "tests" / "sqlite_resume_worker.py"


def _run_worker(mode: str, root: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(PROJECT_ROOT), env.get("PYTHONPATH", "")))
    )
    completed = subprocess.run(
        [sys.executable, str(WORKER), mode, str(root)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert completed.returncode == 0, (
        f"Worker exited with status {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(completed.stdout.strip())


def test_second_python_process_resumes_without_repeating_solver(
    build_temp_path: Path,
) -> None:
    root = build_temp_path / f"sqlite_two_process_{uuid4().hex}"

    first = _run_worker("start", root)
    second = _run_worker("resume", root)

    assert first == {
        "mode": "start",
        "iterations": 1,
        "solver_calls": 1,
        "next": ["decide"],
    }
    assert second == {
        "mode": "resume",
        "iterations": 2,
        "solver_calls": 2,
        "next": ["decide"],
    }
