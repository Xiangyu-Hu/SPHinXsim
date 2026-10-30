"""Standalone implementation of the native window's host contract.

Uses the same immutable jobs and explicit continuation as CLI/MCP. The Qt loop
never performs project admission/copying itself. No second distributed queue or
chat/session database is introduced.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import uuid

from ..export_vtp import atomic_json
from ..jobs import JobStore


class LocalHost:
    def __init__(self, work, entry):
        self.work, self.entry = Path(work), Path(entry)
        self.jobs = JobStore(work)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.intent = None
        self.seen = None

    def execute(self, intent):
        action, job = intent["action"], intent.get("runId")
        if action == "particles":
            return self.jobs.submit(self.entry.relative_to(self.work).as_posix(), kind="particles")["id"]
        if action == "continue":
            state = self.jobs.status(job)
            if state["state"] == "preview_expired":
                # Resubmit the original frozen input, not a changed desktop draft.
                request = json.loads((self.jobs.job_path(job) / "request.json").read_text())
                frozen = self.jobs.job_path(job) / "project" / request["project"]["entryFile"]
                return self.jobs.submit(frozen.relative_to(self.work).as_posix(), kind="run")["id"]
            self.jobs.continue_job(job)
        elif action in ("cancel", "invalidate") and job:
            self.jobs.cancel(job)
        elif action != "save":
            raise ValueError("Unsupported local desktop intent")
        return job or ""

    def tick(self, state, accept):
        intent = state.get("intent")
        if intent and not self.pending:
            self.intent = dict(intent)
            self.pending = self.executor.submit(self.execute, self.intent)
        if self.pending and self.pending.done():
            reply = {"operation": "accept", "version": state["version"], "intentId": self.intent["id"]}
            try:
                reply["runId"] = self.pending.result()
            except Exception as exc:
                reply["error"] = str(exc)[:500]
            accept(reply)
            self.pending = None
        job_id = state.get("runId")
        if not job_id:
            return
        job = self.jobs.status(job_id)
        view = {"runId": job_id, "state": job["state"], "error": job.get("error"), "formal": job["kind"] == "run" or (self.jobs.job_path(job_id) / "control.json").exists()}
        source = self.jobs.job_path(job_id)
        published = source / "published.json"
        if published.exists():
            name = json.loads(published.read_text())["snapshot"]
            if (job_id, name) != self.seen:
                # Reuse the same hash verification exposed to independent clients.
                self.jobs.results(job_id)
                manifest = json.loads((source / "result" / name).read_text())
                directory = self.work / "preview" / str(uuid.uuid4())
                directory.mkdir(parents=True)
                try:
                    shutil.copyfile(source / "effective-config.json", directory / "effective.json")
                    for item in manifest.get("geometry", []):
                        if item.get("source"):
                            shutil.copyfile(source / "result" / item["source"], directory / ("Shape" + item["id"] + ".vtp"))
                    for body, frames in manifest.get("bodies", {}).items():
                        if frames:
                            shutil.copyfile(source / "result" / frames[-1]["source"], directory / (body + ".vtp"))
                except Exception:
                    shutil.rmtree(directory)
                    raise
                self.seen = (job_id, name)
                self.view_directory = directory.name
            view["directory"] = self.view_directory
        (self.work / "preview").mkdir(exist_ok=True)
        atomic_json(self.work / "preview/view.json", view)
