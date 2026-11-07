"""One bounded CAD child per shared desktop; candidates never overwrite human edits."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import subprocess
import time
import uuid


class ModelingSession:
    def __init__(self, root, entry, timeout=120):
        self.root, self.entry, self.timeout = Path(root), Path(entry), timeout
        self.process, self.job = None, None
        self.directory = self.root.parent / "cad"
        self.directory.mkdir(exist_ok=True)

    def persist(self):
        temp = self.directory / (self.job["id"] + ".tmp")
        temp.write_text(json.dumps(self.job, allow_nan=False))
        temp.replace(self.directory / (self.job["id"] + ".json"))

    def command(self, name, args, *, dirty, frozen, automation):
        if name == "geometry_status":
            job_id = args.get("jobId")
            if job_id is None:
                return self.job or {"state": "idle"}
            if not isinstance(job_id, str) or str(uuid.UUID(job_id)) != job_id:
                raise ValueError("Invalid geometry job reference")
            saved = self.directory / (job_id + ".json")
            if not saved.is_file():
                raise ValueError("Geometry job not found")
            job = json.loads(saved.read_text())
            if job["state"] == "building" and (not self.job or job["id"] != self.job["id"]):
                job.update(state="interrupted", error="Desktop restarted during CAD generation; request generation again")
            return job
        if name == "geometry_cancel":
            if self.job and self.job["id"] == args.get("jobId"):
                self.cancel("canceled")
            return self.command("geometry_status", args, dirty=dirty, frozen=frozen, automation=automation)
        if name != "geometry_generate":
            raise ValueError("Unsupported geometry command")
        if dirty or frozen:
            raise ValueError("Save current edits and wait for active execution before generating geometry")
        digest = hashlib.sha256(self.entry.read_bytes()).hexdigest()
        if args.get("expectedHash") != digest:
            raise ValueError("Configuration changed; validate again before generating geometry")
        shape_name = args.get("shapeName")
        if not isinstance(shape_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", shape_name):
            raise ValueError("Use a geometry name of 1–64 letters, digits, underscores or hyphens")
        recipe = args.get("recipe")
        raw = json.dumps(recipe, allow_nan=False).encode()
        if len(raw) > 1 << 20:
            raise ValueError("CAD recipe exceeds 1 MiB")
        self.cancel("superseded")
        job_id = str(uuid.uuid4())
        work = self.directory / job_id
        work.mkdir()
        (work / "request.json").write_bytes(raw)
        self.job = {"id": job_id, "state": "building", "shapeName": shape_name, "expectedHash": digest,
                    "automationId": automation, "startedAt": time.time(), "timeoutSeconds": self.timeout}
        try:
            with (work / "build.log").open("w") as log:
                self.process = subprocess.Popen([os.environ.get("SPHINXSIM_CAD_PYTHON", sys.executable), "-m", "sphinxsim_agent.modeling",
                    "--input", str(work / "request.json"), "--output", str(work / "candidate")],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            self.job.update(state="failed", error=str(exc)[:500])
        self.persist()
        return self.job

    def cancel(self, reason="canceled"):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait()
            except ProcessLookupError:
                self.process.wait()
        self.process = None
        if self.job and self.job["state"] == "building":
            self.job.update(state=reason)
            self.persist()

    def tick(self, *, dirty, frozen, blocked, apply):
        if not self.process:
            return
        if self.job["automationId"] in blocked:
            self.cancel("stopped")
            return
        if time.time() - self.job["startedAt"] > self.timeout:
            self.cancel("timed_out")
            return
        code = self.process.poll()
        if code is None:
            return
        self.process = None
        work = self.directory / self.job["id"] / "candidate"
        try:
            result = json.loads((work / "cad-result.json").read_text())
            if code != 0 or not result.get("ok"):
                raise ValueError(result.get("error", "CAD generation failed"))
            if result.get("meshChecks") != "closed_manifold_finite_nondegenerate":
                raise ValueError("CAD candidate has no mesh-validation evidence")
            self.job.update(state="candidate_ready", result=result)
            if dirty or frozen or hashlib.sha256(self.entry.read_bytes()).hexdigest() != self.job["expectedHash"]:
                self.job.update(state="conflict", error="New geometry is preserved; current edits were not overwritten. Regenerate against the latest saved configuration.")
            else:
                destination = self.entry.parent / "models" / self.job["id"]
                destination.parent.mkdir(exist_ok=True)
                work.rename(destination)
                relative = os.path.relpath(destination / "model.stl", self.entry.parent)
                config = json.loads(self.entry.read_text())
                replacement = {"name": self.job["shapeName"], "type": "triangle_mesh", "file_name": relative}
                for index, shape in enumerate(config["geometries"]["shapes"]):
                    if shape["name"] == self.job["shapeName"]:
                        config["geometries"]["shapes"][index] = replacement
                        break
                else:
                    config["geometries"]["shapes"].append(replacement)
                apply(config)
                self.job.update(state="applied", savedConfigHash=hashlib.sha256(self.entry.read_bytes()).hexdigest(),
                    persistence="pending",
                    files=[str((destination / name).relative_to(self.root)) for name in ("recipe.json", "model.step", "model.stl")],
                    message="Geometry applied locally; preview rebuilding. Host project persistence is pending.")
        except Exception as exc:
            self.job.update(state="failed", error=str(exc)[:1000])
        self.persist()
