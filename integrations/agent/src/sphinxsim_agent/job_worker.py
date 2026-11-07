"""Detached local job owner. Persistent jobs outlive CLI/MCP connections."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .export_vtp import atomic_json


def main():
    def interrupted(_signum, _frame):
        raise SystemExit("Local supervisor interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    work = Path(sys.argv[1]).resolve(strict=True)
    state = json.loads((work / "job.json").read_text())
    process = None
    try:
        with (work / "solver.log").open("wb") as log:
            command = ([os.environ.get("SPHINXSIM_CAD_PYTHON", sys.executable), "-m", "sphinxsim_agent.modeling", "--input", str(work / "recipe.json"), "--output", str(work / "cad")]
                       if state["kind"] == "cad" else [sys.executable, "-m", "sphinxsim_agent.runner", str(work / "request.json")])
            process = subprocess.Popen(command,
                cwd=work, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        while process.poll() is None:
            if (work / "cancel.json").exists() or time.monotonic() - started > state["wallSeconds"]:
                state["state"] = "canceled" if (work / "cancel.json").exists() else "failed"
                if state["state"] == "failed":
                    state["error"] = "Wall-clock execution budget exceeded"
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                break
            published = work / "published.json"
            if published.exists():
                state.update(json.loads(published.read_text()))
            elif state["state"] == "queued":
                state["state"] = "preparing"
            state["heartbeat"] = time.time()
            atomic_json(work / "job.json", state)
            time.sleep(.2)
        if state["state"] not in ("canceled", "failed"):
            if process.returncode != 0:
                state.update(state="failed", error="Native execution failed; inspect bounded job logs")
            elif state["kind"] == "cad":
                result = json.loads((work / "cad/cad-result.json").read_text())
                state["state"] = "succeeded" if result.get("ok") else "failed"
            elif (work / "published.json").exists():
                state.update(json.loads((work / "published.json").read_text()))
                if state["state"] not in ("succeeded", "preview_ready", "preview_expired"):
                    state.update(state="failed", error="Execution exited without verified completion")
            else:
                state.update(state="failed", error="Execution produced no verified output")
    except Exception as exc:
        state.update(state="failed", error=str(exc)[:500])
    except BaseException:
        state.update(state="interrupted", error="Local supervisor interrupted; automatic replay is disabled")
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        state["heartbeat"] = time.time()
        atomic_json(work / "job.json", state)


if __name__ == "__main__":
    main()
