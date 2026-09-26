"""Durable local execution: one worker per workspace, queryable from any UI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from sphinxsim.services.process_control import alive, identity, terminate_tree, workspace_lock
from .models import utc_now
from .store import AgentTaskStore


class RunManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.workspace / "execution"
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, workflow_id):
        self.runtime.load_settings(workflow_id)  # also validates the identifier
        return self.root / workflow_id

    def _read(self, path):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _save(self, path, value):
        AgentTaskStore._atomic_write(path, json.dumps(value, indent=2))

    def event(self, workflow_id, kind, **data):
        path = self.root / "events.sqlite"
        with sqlite3.connect(path, timeout=30) as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, workflow TEXT, time TEXT, kind TEXT, data TEXT)")
            db.execute("INSERT INTO events(workflow,time,kind,data) VALUES(?,?,?,?)",
                       (workflow_id, utc_now().isoformat(), kind, json.dumps(data)))

    def events(self, workflow_id, after=0):
        self.directory(workflow_id)
        path = self.root / "events.sqlite"
        if not path.exists():
            return []
        with sqlite3.connect(path, timeout=30) as db:
            rows = db.execute("SELECT id,time,kind,data FROM events WHERE workflow=? AND id>? ORDER BY id",
                              (workflow_id, after)).fetchall()
        return [dict(id=i, time=t, kind=k, data=json.loads(d)) for i,t,k,d in rows]

    def status(self, workflow_id):
        directory = self.directory(workflow_id)
        record = self._read(directory / "worker.json")
        if record.get("state") in {"starting", "running", "cancelling"} and not alive(record):
            record = dict(record, state="interrupted")
        record["attempts"] = self.attempts(workflow_id)
        return record

    def attempts(self, workflow_id):
        return sorted([self._read(p) for p in self.directory(workflow_id).glob("attempts/*/attempt.json")],
                      key=lambda r: r["started_at"])

    def submit(self, workflow_id, *, action="approve"):
        if action not in {"approve", "resume"}:
            raise ValueError("worker action must be approve or resume")
        directory = self.directory(workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        with workspace_lock(self.root / "workspace.lock"):
            for p in self.root.glob("*/worker.json"):
                existing = self._read(p)
                if alive(existing):
                    raise RuntimeError("An Agent worker is already active in this workspace")
            service = self.runtime.create_service(self.runtime.load_settings(workflow_id), starting=False, execution_enabled=False)
            status = service.status(workflow_id)
            if action == "approve" and not status.awaiting_approval:
                raise ValueError("workflow is not awaiting approval")
            if action == "resume" and (status.awaiting_approval or status.status.value != "running"):
                raise ValueError("only an interrupted running workflow can be resumed")
            # A dead worker can leave a solver alive. Stop only recorded matching identities.
            for attempt in self.attempts(workflow_id):
                if attempt.get("status") == "running":
                    terminate_tree(attempt)
            (directory / "cancel.request").unlink(missing_ok=True)
            token = uuid4().hex
            command = [sys.executable, "-m", "sphinxsim.agent.worker", str(self.runtime.project_root),
                       str(self.runtime.workspace), workflow_id, action, token]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.runtime.project_root) + os.pathsep + env.get("PYTHONPATH", "")
            with (directory / "worker.log").open("ab") as log:
                process = subprocess.Popen(command, cwd=self.runtime.project_root, env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
                    start_new_session=os.name != "nt")
            record = dict(identity(process.pid), workflow_id=workflow_id, token=token,
                          state="starting", action=action, started_at=utc_now().isoformat())
            self._save(directory / "worker.json", record)
            self.event(workflow_id, "worker_submitted", action=action, pid=process.pid)
        return record

    def cancel(self, workflow_id):
        directory = self.directory(workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cancel.request").touch()
        self.event(workflow_id, "cancellation_requested")
        record = self.status(workflow_id)
        if record.get("state") in {"starting", "running", "cancelling"}:
            # The child polls this durable signal and reaps its own solver tree.
            return dict(record, state="cancelling")
        for attempt in self.attempts(workflow_id):
            if attempt.get("status") == "running":
                terminate_tree(attempt)
        return record

    def wait(self, workflow_id):
        while True:
            status = self.status(workflow_id)
            if status.get("state") not in {"starting", "running", "cancelling"}:
                return status
            time.sleep(0.2)

    def run(self, workflow_id, action, token):
        directory = self.directory(workflow_id)
        # Parent owns the lock until it has persisted our identity and token.
        for _ in range(100):
            record = self._read(directory / "worker.json")
            if record.get("token") == token:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("worker launch was not registered")
        with workspace_lock(self.root / "workspace.lock"):
            record.update(state="running")
            self._save(directory / "worker.json", record)
            self.event(workflow_id, "worker_started")
            try:
                service = self.runtime.create_service(self.runtime.load_settings(workflow_id), starting=False)
                if action == "resume":
                    from .executor import StaticPlanExecutor
                    from .models import TaskStatus, StepStatus
                    result = service.workflow.load(workflow_id)
                    task_id = f"{workflow_id}_iteration_{len(result.iterations)+1:03d}"
                    from .store import TaskNotFoundError
                    try:
                        task = self.runtime.store.load_task(task_id)
                        plan = self.runtime.store.load_active_plan(task_id)
                        if task.status == TaskStatus.RUNNING and any(s.status == StepStatus.IN_PROGRESS for s in plan.steps):
                            StaticPlanExecutor(self.runtime.store, service.workflow.command_adapter).recover_interrupted(task_id)
                    except TaskNotFoundError:
                        pass
                if (directory / "cancel.request").exists():
                    status = service.cancel(workflow_id)
                else:
                    status = getattr(service, action)(workflow_id)
                record.update(state=status.status.value, finished_at=utc_now().isoformat())
                self.event(workflow_id, "worker_finished", status=status.status.value)
            except BaseException as exc:
                record.update(state="interrupted", error_message=str(exc), finished_at=utc_now().isoformat())
                self.event(workflow_id, "worker_interrupted", error_message=str(exc))
            finally:
                self._save(directory / "worker.json", record)
