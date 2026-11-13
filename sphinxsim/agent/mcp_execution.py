"""Execute workflow actions through native MCP operations and persistent jobs."""
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from sphinxsim.runtime.client import MCPClient
from sphinxsim.runtime.export_vtp import atomic_json
from sphinxsim.runtime.jobs import TERMINAL
from sphinxsim.services import CLICommandResult


class MCPExecutionClient(MCPClient):
    def __init__(self, *, cwd, root=None, environment=None, default_timeout=300, control_dir=None):
        super().__init__(root or cwd, environment=environment, timeout=default_timeout)
        self.cwd = Path(cwd).resolve()
        self.control_dir = Path(control_dir) if control_dir else None

    def relative(self, value):
        path = Path(value)
        return super().relative(path if path.is_absolute() else self.cwd / path)

    def _result(self, operation, arguments, execute, timeout=None):
        started = time.monotonic()
        directory = None
        mcp_operation = {"generate": "config_generate", "update": "config_update",
                         "run": "submit", "preview": "submit"}.get(operation, operation)
        record = {
            "operation": operation,
            "transport": "mcp",
            "mcp_operation": mcp_operation,
            "command": ["mcp", mcp_operation],
            "workspace": str(self.root),
            "config_path": arguments.get("config_path"),
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "timeout_seconds": timeout or self.timeout,
        }
        if self.control_dir:
            directory = self.control_dir / "attempts" / uuid4().hex
            directory.mkdir(parents=True)
            atomic_json(directory / "attempt.json", record)
        code, stderr, data = 0, "", {}
        try:
            if self.control_dir and (self.control_dir / "cancel.request").exists():
                data = {"cancelled": True}
            else:
                data = execute(record, directory)
            code = 1 if data.get("cancelled") or data.get("timed_out") else 0
        except Exception as exc:
            while getattr(exc, "exceptions", None):
                exc = exc.exceptions[0]
            code, stderr = 1, str(exc)
            if isinstance(exc, TimeoutError):
                data = {"timed_out": True}
                stderr = stderr or f"MCP operation {mcp_operation} timed out"
        stdout = json.dumps(data, allow_nan=False)
        failure_kind = None
        if data.get("cancelled"):
            failure_kind = "cancelled"
        elif data.get("timed_out"):
            failure_kind = "solver_timeout" if operation == "run" else "command_timeout"
        elif code:
            failure_kind = {"generate": "generation_error", "validate": "validation_error",
                            "run": "solver_process_error", "preview": "preview_render_error"}.get(operation, "operation_error")
        record.update(
            status="completed" if code == 0 else "failed",
            returncode=code,
            failure_kind=failure_kind,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.monotonic() - started,
        )
        if directory:
            (directory / "stdout.log").write_text(stdout, encoding="utf-8")
            (directory / "stderr.log").write_text(stderr, encoding="utf-8")
            record.update(stdout_path=str(directory / "stdout.log"), stderr_path=str(directory / "stderr.log"))
            atomic_json(directory / "attempt.json", record)
        return CLICommandResult(
            command=("mcp", mcp_operation),
            returncode=code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=bool(data.get("timed_out")),
            cancelled=bool(data.get("cancelled")),
            attempt_path=str(directory / "attempt.json") if directory else None,
        )

    def generate(self, description, output_path, *, timeout=None):
        arguments = {"description": description, "output": self.relative(output_path)}
        return self._result("generate", {}, lambda *_: self.call("config_generate", arguments, timeout=timeout), timeout)

    def update(self, config_path, instruction, *, output_path=None, timeout=None, **kwargs):
        arguments = {"config_file": self.relative(config_path), "description": instruction,
                     "output": self.relative(output_path or config_path)}
        return self._result("update", {}, lambda *_: self.call("config_update", arguments, timeout=timeout), timeout)

    def validate(self, config_path, *, timeout=None):
        arguments = {"config_file": self.relative(config_path)}
        return self._result("validate", {}, lambda *_: self.call("validate", arguments, timeout=timeout), timeout)

    def prepare_case(self, source_config_path, output_config_path):
        self.call("case_prepare", {"config_file": self.relative(source_config_path), "output": self.relative(output_config_path)})
        return self.root / self.relative(output_config_path)

    def update_friction_angle(self, config_path, friction_angle_degrees, *, body_name):
        result = self.call("set_friction_angle", {"config_file": self.relative(config_path),
            "body_name": body_name, "friction_angle_degrees": friction_angle_degrees})
        return self.root / result["config_file"], result["friction_angle_radians"]

    def run(self, config_path, *, output_dir=None, timeout=None):
        if output_dir is None:
            raise ValueError("MCP workflow execution requires an explicit output directory")
        return self._job(config_path, "run", output_dir, timeout=timeout)

    def preview(self, config_path, *, with_particles=False, off_screen=False, screenshot_path=None, timeout=None):
        if screenshot_path is None:
            raise ValueError("MCP visual preflight requires a screenshot output")
        screenshot = Path(screenshot_path)
        staged = self.prepare_case(config_path, screenshot.parent / (screenshot.stem + "-project") / "case.json")
        return self._job(staged, "particles" if with_particles else "geometry",
                         screenshot.parent / (screenshot.stem + "-job"),
                         screenshot_path=screenshot_path, timeout=timeout)

    def _job(self, config_path, kind, output_dir, *, screenshot_path=None, timeout=None):
        seconds = timeout or self.timeout
        config_file = self.relative(config_path)
        output = self.root / self.relative(output_dir) if output_dir else None
        async def execute(record, directory):
            async with self.session() as session:
                request = lambda name, arguments: self.request(session, name, arguments)
                project = await request("project_inspect", {"config_file": config_file})
                config_sha256 = next(item["sha256"] for item in project["files"] if item["path"] == project["entry_file"])
                descriptor = output / "job-reference.json" if output else None
                previous = json.loads(descriptor.read_text(encoding="utf-8")) if descriptor and descriptor.exists() else None
                reference = previous["reference"] if previous else None
                state = await request("job_status", {"reference": reference}) if reference else None
                if reference and previous["config_sha256"] != config_sha256:
                    raise ValueError("Configuration changed since this run was submitted")
                if state is None or state["state"] in ("failed", "canceled", "interrupted", "preview_expired"):
                    policy = {"config_file": config_file, "project": project, "kind": kind,
                        "wall_seconds": min(259200, max(1, math.ceil(seconds))),
                        "lease_seconds": min(86400, max(1, math.ceil(seconds)))}
                    intent = output / "submission.json" if output else None
                    if intent and intent.exists() and previous is None:
                        submission = json.loads(intent.read_text(encoding="utf-8"))
                        if {key: value for key, value in submission.items() if key != "submission_id"} != policy:
                            raise ValueError("Submission inputs changed since the previous request")
                    else:
                        submission = {**policy, "submission_id": str(uuid4())}
                        if intent:
                            atomic_json(intent, submission)
                    record["submission_id"] = submission["submission_id"]
                    if directory:
                        atomic_json(directory / "attempt.json", record)
                    state = await request("submit", submission)
                    reference = state["reference"]
                record.update(reference=reference,
                    output_dir=str(self.root / ".sphinxsim/jobs" / reference["job_id"] / "solver"))
                if descriptor:
                    atomic_json(descriptor, {"workspace": str(self.root), "reference": reference,
                        "config_sha256": config_sha256})
                if directory:
                    atomic_json(directory / "attempt.json", record)
                started = time.monotonic()
                try:
                    while state["state"] not in TERMINAL and state["state"] != "waiting":
                        cancelled = self.control_dir is not None and (self.control_dir / "cancel.request").exists()
                        timed_out = time.monotonic() - started >= seconds
                        if cancelled or timed_out:
                            await request("job_cancel", {"reference": reference})
                            for _ in range(50):
                                state = await request("job_status", {"reference": reference})
                                if state["state"] in TERMINAL:
                                    break
                                await asyncio.sleep(.1)
                            return {"reference": reference, "cancelled": bool(cancelled), "timed_out": timed_out}
                        await asyncio.sleep(.25)
                        state = await request("job_status", {"reference": reference})
                    if state["state"] == "canceled":
                        return {"reference": reference, "cancelled": True}
                    if state["state"] in ("failed", "interrupted", "preview_expired"):
                        logs = await request("job_logs", {"reference": reference})
                        raise ValueError(state.get("error", "Native MCP job failed") + "\n" + logs["text"])
                    result = await request("results_inspect", {"reference": reference})
                    if screenshot_path:
                        result["screenshot"] = await request("results_render", {
                            "reference": reference, "output": self.relative(screenshot_path)})
                    return {"reference": reference, "results": result}
                finally:
                    if screenshot_path:
                        await request("job_cancel", {"reference": reference})
        return self._result("run" if kind == "run" else "preview", {"config_path": str(self.root / config_file)},
            lambda record, directory: asyncio.run(execute(record, directory)), seconds)
