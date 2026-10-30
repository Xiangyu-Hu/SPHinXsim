"""Single-machine execution, shared by CLI, MCP and the standalone desktop.

No tenant, chat or cluster state lives here. The caller owns this workspace and
its operating-system/container resource limits. Cancellation is a request to a
live supervisor, never a signal to a persisted PID that might have been reused.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .export_vtp import atomic_json, hash_file
from .project_input import load_project

TERMINAL = {"succeeded", "failed", "canceled", "preview_ready", "preview_expired", "interrupted"}
SUFFIXES = {".json", ".stl", ".step", ".md", ".txt"}


class JobStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = self.root / ".sphinxsim/jobs"
        if (self.root / ".sphinxsim").is_symlink() or self.directory.is_symlink():
            raise ValueError("Job store symlinks are forbidden")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, relative, *, exists=True):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Use a path relative to the configured workspace")
        target = self.root / relative
        candidates = []
        current = target
        while current != self.root and current.is_relative_to(self.root):
            candidates.append(current)
            current = current.parent
        if ".." in Path(relative).parts or any(p.is_symlink() for p in candidates):
            raise ValueError("Workspace symlinks and parent traversal are forbidden")
        resolved = target.resolve(strict=exists)
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("Path outside workspace")
        return resolved

    def job_path(self, job_id):
        if not isinstance(job_id, str) or str(uuid.UUID(job_id)) != job_id:
            raise ValueError("Invalid job reference")
        path = self.directory / job_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Job not found")
        return path

    @contextmanager
    def lock(self):
        with (self.directory / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def submit(self, entry, *, kind="run", lease_seconds=600, refresh_seconds=10, wall_seconds=3600):
        if kind not in ("geometry", "particles", "run"):
            raise ValueError("Use geometry, particles or run")
        if not 1 <= lease_seconds <= 86400 or not 1 <= refresh_seconds <= 3600 or not 1 <= wall_seconds <= 259200:
            raise ValueError("Execution policy outside supported limits")
        entry = self.path(entry)
        with self.lock():
            active = [p for p in self.directory.iterdir() if p.is_dir() and (p / "job.json").is_file() and self.status(p.name)["state"] not in TERMINAL - {"interrupted"}]
            if len(active) >= 4:
                raise ValueError("Local executor capacity reached (4 active jobs)")
            job_id = str(uuid.uuid4())
            work = self.directory / job_id
            project = work / "project"
            project.mkdir(parents=True)
            try:
                files, total = [], 0
                # The entry's directory is the project root, not the whole workspace.
                for source in sorted(entry.parent.rglob("*")):
                    relative = source.relative_to(entry.parent)
                    if any(part.startswith(".") for part in relative.parts):
                        continue
                    if source.is_symlink():
                        raise ValueError("Project symlinks are forbidden")
                    if not source.is_file() or source.suffix.lower() not in SUFFIXES:
                        continue
                    size = source.stat().st_size
                    total += size
                    if size > 512 << 20 or total > 2 << 30 or len(files) >= 128:
                        raise ValueError("Project exceeds file/count/total budget")
                    target = project / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    files.append({"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": hash_file(target)})
                snapshot = {"entryFile": entry.name, "files": files}
                # Admission before submission; the runner independently rechecks frozen bytes.
                load_project(snapshot, project)
                request = {"version": 1, "project": snapshot, "kind": kind, "attempt": job_id,
                           "previewLeaseSeconds": lease_seconds, "refreshIntervalSeconds": refresh_seconds}
                atomic_json(work / "request.json", request)
                atomic_json(work / "job.json", {"version": 1, "id": job_id, "kind": kind, "state": "queued",
                    "createdAt": time.time(), "heartbeat": time.time(), "wallSeconds": wall_seconds})
                with (work / "supervisor.log").open("wb") as log:
                    subprocess.Popen([sys.executable, "-m", "sphinxsim_agent.job_worker", str(work)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except Exception:
                shutil.rmtree(work)
                raise
        return self.status(job_id)

    def status(self, job_id):
        work = self.job_path(job_id)
        state = json.loads((work / "job.json").read_text())
        if state["state"] not in TERMINAL and time.time() - state["heartbeat"] > 15:
            return {**state, "state": "interrupted", "error": "Local supervisor unavailable; no inputs will be replayed"}
        return state

    def generate(self, recipe):
        raw = json.dumps(recipe, allow_nan=False).encode()
        if len(raw) > 1 << 20:
            raise ValueError("CAD recipe exceeds 1 MiB")
        with self.lock():
            active = [p for p in self.directory.iterdir() if p.is_dir() and (p / "job.json").is_file() and self.status(p.name)["state"] not in TERMINAL - {"interrupted"}]
            if len(active) >= 4:
                raise ValueError("Local executor capacity reached (4 active jobs)")
            job_id = str(uuid.uuid4())
            work = self.directory / job_id
            work.mkdir()
            (work / "recipe.json").write_bytes(raw)
            atomic_json(work / "job.json", {"version": 1, "id": job_id, "kind": "cad", "state": "queued",
                "createdAt": time.time(), "heartbeat": time.time(), "wallSeconds": 120})
            try:
                with (work / "supervisor.log").open("wb") as log:
                    subprocess.Popen([sys.executable, "-m", "sphinxsim_agent.job_worker", str(work)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except Exception:
                shutil.rmtree(work)
                raise
        return self.status(job_id)

    def continue_job(self, job_id):
        with self.lock():
            state = self.status(job_id)
            work = self.job_path(job_id)
            if (work / "control.json").exists():
                return state  # One frozen execution; repeated continue never spawns another.
            if state["state"] != "waiting":
                raise ValueError("Job is not waiting for continuation")
            from datetime import datetime, timezone
            if datetime.fromisoformat(state["leaseExpiresAt"]) <= datetime.now(timezone.utc):
                raise ValueError("Particle lease expired; submit the frozen project as a new job")
            atomic_json(work / "control.json", {"command": "continue", "attempt": job_id})
            return {**state, "continuationRequested": True}

    def cancel(self, job_id):
        state = self.status(job_id)
        if state["state"] not in TERMINAL:
            atomic_json(self.job_path(job_id) / "cancel.json", {"requested": True})
        return {**state, "cancellationRequested": state["state"] not in TERMINAL}

    def logs(self, job_id):
        path = self.job_path(job_id) / "solver.log"
        if not path.exists():
            return {"text": ""}
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16384))
            return {"text": stream.read(16384).decode(errors="replace")}

    def results(self, job_id):
        work = self.job_path(job_id)
        state = self.status(job_id)
        if state["kind"] == "cad":
            result = work / "cad/cad-result.json"
            if not result.exists():
                return {"job": state, "available": False}
            content = json.loads(result.read_text())
            for item in content.get("files", []):
                name = item["path"]
                if name not in ("model.stl", "model.step", "recipe.json"):
                    raise ValueError("Invalid CAD artifact reference")
                path = work / "cad" / name
                if path.is_symlink() or path.stat().st_size != item["bytes"] or hash_file(path) != item["sha256"]:
                    raise ValueError("CAD artifact size/hash mismatch")
                item["resource"] = path.as_uri()
            return {"job": state, "available": bool(content.get("ok")), "geometry": content}
        published = work / "published.json"
        if not published.exists():
            return {"job": state, "available": False}
        name = json.loads(published.read_text())["snapshot"]
        from .results import inspect_results
        return {"job": state, "available": True, **inspect_results(work / "result", name)}

    def artifact(self, job_id, name):
        """Only verified published files are readable through MCP resources."""
        result = self.results(job_id)
        if not result.get("available"):
            raise ValueError("No verified artifacts available")
        work = self.job_path(job_id)
        if result["job"]["kind"] == "cad":
            allowed = {item["path"] for item in result["geometry"].get("files", [])}
            directory = work / "cad"
        else:
            directory = work / "result"
            allowed = {result["manifest"]}
            def collect(value):
                if isinstance(value, dict):
                    if {"file", "bytes", "sha256"}.issubset(value):
                        allowed.add(value["file"])
                    if "source" in value:
                        allowed.add(value["source"])
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
            collect(json.loads((directory / result["manifest"]).read_text()))
        if name not in allowed or Path(name).name != name:
            raise ValueError("Artifact is not in the verified publication")
        path = directory / name
        if path.is_symlink() or path.stat().st_size > 1 << 20:
            raise ValueError("Artifact exceeds 1 MiB MCP resource budget; use owner-managed file delivery")
        return path.read_bytes()
