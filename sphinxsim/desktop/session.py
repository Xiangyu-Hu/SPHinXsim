"""Native editor commands, independent of remote desktops and input providers.

Qt owns editing/rendering. Detached local jobs own calculations. Every command
names the observed instance/version; a job action also names its frozen job.
"""
import base64
from concurrent.futures import Future
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import uuid

from ..runtime.export_vtp import atomic_json, hash_file
from ..runtime.jobs import JobStore
from ..runtime.physics import check_config


class Session:
    def __init__(self, root, entry, runtime):
        self.jobs = JobStore(root)
        self.entry = self.jobs.path(entry)
        self.runtime = runtime
        self.instance_id = str(uuid.uuid4())
        self.version = 0
        self.signature = None
        self.saved_hash = hash_file(self.entry)
        self.blocked = set()
        self.reference = None
        self.manifest = None
        self.read_only = False
        self.error = None

    def status(self):
        editor = self.runtime._json_editor
        try:
            payload = editor["payload"]()
        except (ValueError, TypeError):
            payload = {"invalid_editor_value": str(sys.exc_info()[1])}
        signature = (hash_file(self.entry), json.dumps(payload, sort_keys=True, default=str), editor["is_dirty"](), self.read_only)
        if signature != self.signature:
            self.version += 1
            self.signature = signature
        return {"instance_id": self.instance_id, "version": self.version,
            "saved_hash": signature[0], "configuration": payload, "dirty": editor["is_dirty"](),
            "read_only": self.read_only, "reference": self.reference, "manifest": self.manifest, "error": self.error}

    def save(self, path, config, file_content=None):
        if self.read_only or hash_file(path) != self.saved_hash:
            raise OSError("Configuration changed externally; unsaved edits are preserved")
        from ..cli import _write_validated_config
        try:
            check_config(config.model_dump(mode="json", exclude_none=True))
        except ValueError as exc:
            raise OSError(str(exc)) from exc
        result = _write_validated_config(path, config, file_content)
        self.saved_hash = hash_file(path)
        return result

    def handle(self, command):
        from ..config.schemas import SimulationConfig
        operation = command["operation"]
        if operation == "status":
            return self.status()
        state = self.status()
        if command.get("instance_id") != self.instance_id:
            raise ValueError("Desktop instance changed; observe again")
        automation = command.get("automation_id")
        if operation == "stop":
            if not automation:
                raise ValueError("Specify the automation_id to stop")
            self.blocked.add(automation)
            return {"stopped": True, "automation_id": automation}
        if not automation or automation in self.blocked:
            raise ValueError("Missing or stopped automation scope; observe before starting new operations")
        if command.get("version") != state["version"]:
            raise ValueError("Editor changed; observe and review the current version")
        editor = self.runtime._json_editor
        if operation == "screenshot":
            from qtpy import QtCore
            window = self.runtime.plotter.app_window
            origin = window.mapToGlobal(QtCore.QPoint(0, 0))
            pixmap = window.screen().grabWindow(0, origin.x(), origin.y(), window.width(), window.height())
            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            if pixmap.isNull() or not pixmap.save(buffer, "PNG"):
                raise ValueError("Native screenshot unavailable on this display")
            return {"instance_id": self.instance_id, "version": state["version"],
                "content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(bytes(buffer.data())).decode()}]}
        if operation == "save":
            if self.read_only:
                raise ValueError("Historical configuration is read-only")
            if command.get("expected_hash") != state["saved_hash"]:
                raise ValueError("Saved configuration changed; observe again")
            editor["shortcut"].activated.emit()
            if editor["is_dirty"]():
                raise ValueError(editor["status"].text())
            return self.status()
        if operation in ("continue", "cancel", "result"):
            reference = command.get("reference")
            job_id = self.jobs.resolve(reference)
            if operation == "result":
                if editor["is_dirty"]():
                    raise ValueError("Unsaved edits are preserved; save before opening historical results")
                if not self.jobs.results(job_id).get("available"):
                    raise ValueError("No verified output is available for this job")
                self.reference, self.manifest, self.read_only = reference, None, True
                self.version += 1
                return self.status()
            result = self.jobs.continue_job(job_id) if operation == "continue" else self.jobs.cancel(job_id)
            return {"job": result, "reference": reference}
        if editor["is_dirty"]():
            raise ValueError("Unsaved native edits are preserved; save before applying another operation")
        if operation == "edit":
            if command.get("expected_hash") != state["saved_hash"]:
                raise ValueError("Source configuration changed; observe again")
            model = SimulationConfig.model_validate_json(self.entry.read_text())
            self.read_only, self.reference, self.manifest = False, None, None
            self.saved_hash = hash_file(self.entry)
            editor["refresh"](model, self.entry, False)
            editor["tree"].setEnabled(True)
            return self.status()
        if operation == "apply":
            if self.read_only or command.get("expected_hash") != state["saved_hash"]:
                raise ValueError("Configuration changed or is read-only")
            config, report = check_config(command["configuration"])
            if report["normalizations"]:
                raise ValueError("Compile and review configuration changes before applying")
            model = SimulationConfig.model_validate(config)
            self.save(self.entry, model)
            editor["refresh"](model, self.entry, False)
            return self.status()
        if operation in ("preview", "particles"):
            if self.read_only or command.get("expected_hash") != state["saved_hash"]:
                raise ValueError("Configuration changed or is read-only")
            if not command.get("submission_id"):
                raise ValueError("Provide a submission_id UUID to make retries idempotent")
            relative = self.entry.relative_to(self.jobs.root).as_posix()
            project = self.jobs.inspect_project(relative)
            job = self.jobs.submit(relative, expected_project=project, submission_id=command["submission_id"], kind="geometry" if operation == "preview" else "particles")
            self.reference = job["reference"]
            self.manifest = None
            self.version += 1
            return {"job": job, "desktop": self.status()}
        raise ValueError("Unsupported native application operation")

    def refresh_result(self):
        """Render verified output without discarding edits or resetting the camera."""
        if self.reference is None:
            return
        job_id = self.jobs.resolve(self.reference)
        pointer = self.jobs.job_path(job_id) / "published.json"
        if not pointer.exists() or json.loads(pointer.read_text()).get("snapshot") == self.manifest:
            return
        result = self.jobs.results(job_id)
        if not result.get("available") or self.manifest == result["manifest"]:
            return
        import numpy as np
        import pyvista as pv
        from ..config.schemas import SimulationConfig
        from ..visualization.preview import ConfigVisualizer
        work = self.jobs.job_path(self.reference["job_id"])
        source = work / "result"
        manifest = json.loads((source / result["manifest"]).read_text())
        scene = work / "native-view"
        scene.mkdir(exist_ok=True)
        for item in manifest["geometry"]:
            if "indices" in item:
                vertices = np.fromfile(source / item["positions"]["file"], dtype="<f4").reshape(-1, 3)
                triangles = np.fromfile(source / item["indices"]["file"], dtype="<u4").reshape(-1, 3)
                faces = np.column_stack((np.full(len(triangles), 3), triangles)).ravel()
                pv.PolyData(vertices, faces).save(scene / ("Shape" + item["id"] + ".vtp"))
        config = SimulationConfig.model_validate_json((work / "effective-config.json").read_text())
        visualizer = ConfigVisualizer(config, work, config_path=work / "effective-config.json", off_screen=False)
        bodies = {name: source / frames[-1]["source"] for name, frames in manifest["bodies"].items() if frames}
        plotter = self.runtime.plotter
        camera = plotter.camera_position
        previous = dict(plotter.renderer.actors)
        plotter.suppress_rendering = True
        try:
            plotter.clear()
            visualizer._populate_plotter(plotter, scene, bodies)
            self.runtime._install_annotation_hover(visualizer)
            if self.manifest is None:
                visualizer._configure_default_view(plotter, visualizer._spatial_dim())
                plotter.reset_camera(render=False)
            else:
                plotter.camera_position = camera
        except Exception:
            plotter.clear()
            for actor in previous.values():
                plotter.renderer.add_actor(actor, reset_camera=False, render=False)
            plotter.camera_position = camera
            raise
        finally:
            plotter.suppress_rendering = False
        plotter.render()
        self.manifest, self.error = result["manifest"], None
        if self.read_only:
            # Historical views show their frozen configuration, never the draft.
            self.runtime._json_editor["refresh"](config, work / "effective-config.json", False)
            self.runtime._json_editor["tree"].setEnabled(False)


def serve(root, entry, reference_file, *, locale="en", theme="dark"):
    from pydantic import model_validator
    from qtpy import QtCore, QtWidgets
    from pyvistaqt import BackgroundPlotter
    from ..cli import _ShellPreviewRuntime
    from ..config.schemas import SimulationConfig

    class AdmittedConfig(SimulationConfig):
        @model_validator(mode="before")
        @classmethod
        def admit(cls, payload):
            normalized, report = check_config(payload)
            if report["normalizations"]:
                raise ValueError("Compile and review normalized values before saving")
            return normalized

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    runtime = _ShellPreviewRuntime(config_type=AdmittedConfig)
    session = Session(root, entry, runtime)
    runtime.write_config = session.save
    runtime.plotter = BackgroundPlotter(title="SPHinXsim", show=True, window_size=(1400, 900), auto_update=False)
    runtime.plotter.app_window.showMaximized()
    runtime._using_background_plotter = True
    runtime._install_json_editor(SimulationConfig.model_validate_json(session.entry.read_text()), config_path=session.entry, with_particles=False)
    # Saving in the native editor remains an explicit save. Geometry can be
    # refreshed through the Preview action without invalidating another job.
    runtime.show_or_update = lambda *args, **kwargs: 0
    pending = queue.Queue(maxsize=32)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            future = Future()
            try:
                if self.path != "/control" or self.headers.get("Authorization") != "Bearer " + token:
                    self.send_error(403)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1 << 20:
                    raise ValueError("Invalid native request size")
                command = json.loads(self.rfile.read(size))
                from ..operations import TOOLS
                from jsonschema import Draft202012Validator
                Draft202012Validator(TOOLS["desktop"][1]).validate(command)
                if command["operation"] == "stop" and command.get("instance_id") == session.instance_id:
                    if not command.get("automation_id"):
                        raise ValueError("Specify automation_id")
                    session.blocked.add(command["automation_id"])
                    value = {"stopped": True}
                else:
                    pending.put_nowait((future, command))
                    value = future.result(timeout=23)
                raw = json.dumps({"ok": True, "data": value}, allow_nan=False).encode()
            except Exception as exc:
                future.cancel()
                raw = json.dumps({"ok": False, "error": str(exc)[:1000]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    reference_path = Path(reference_file).resolve()
    descriptor = os.open(reference_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"url": f"http://127.0.0.1:{server.server_port}/control", "instance_id": session.instance_id, "token": token}, output)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def tick():
        try:
            future, command = pending.get_nowait()
        except queue.Empty:
            pass
        else:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(session.handle(command))
                except Exception as exc:
                    future.set_exception(exc)
        try:
            session.refresh_result()
        except Exception as exc:
            session.error = "Latest output unavailable; previous view retained. " + str(exc)[:300]
            runtime._json_editor["status"].setText(session.error)

    toolbar = runtime.plotter.app_window.addToolBar("Simulation")
    actions = []
    for operation in ("preview", "particles", "continue", "cancel"):
        def trigger(_checked=False, action=operation):
            state = session.status()
            try:
                session.handle({"operation": action, "instance_id": state["instance_id"], "version": state["version"],
                    "expected_hash": state["saved_hash"], "reference": session.reference, "automation_id": "native-human", "submission_id": str(uuid.uuid4())})
            except Exception as exc:
                runtime._json_editor["status"].setText(str(exc))
        action = toolbar.addAction(operation.title())
        action.triggered.connect(trigger)
        actions.append((operation, {"preview": "刷新预览", "particles": "生成粒子", "continue": "继续仿真", "cancel": "取消计算"}[operation], operation.title(), action))
    from .presentation import Presentation
    presentation = Presentation(runtime, actions)
    presentation.apply(locale, theme)
    for _, _, _, action in actions:
        toolbar.removeAction(action)
    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    print(f"Native desktop reference: {reference_path}", file=sys.stderr)
    try:
        return app.exec()
    finally:
        timer.stop()
        server.shutdown()
        reference_path.unlink(missing_ok=True)
        # Jobs deliberately outlive the native window.
