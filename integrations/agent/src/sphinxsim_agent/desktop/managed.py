"""Host-managed native editor. Geometry work runs in a disposable subprocess.

Qt owns rendering and editing only. Formal/retained particle execution stays in
The selected host's solver queue; closing this window never owns those processes.
"""
import base64
from concurrent.futures import Future
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tempfile
import urllib.error
import urllib.request
import uuid

WORK = Path(os.environ.get("SPHINXSIM_WORK_ROOT", "/work"))
ROOT = WORK / "project"
STATE = WORK / "control/desktop-state.json"
PENDING = queue.Queue(maxsize=32)
ALLOWED = {"get_desktop_state", "list_windows", "get_window_state", "get_screen_size", "get_cursor_position", "click", "scroll", "drag", "move_cursor", "type_text", "press_key", "hotkey", "invoke_menu", "verify_state"}


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".state-", delete=False) as output:
        temp = Path(output.name)
        json.dump(value, output)
    temp.replace(path)


def driver_request(path, body=None, method="POST", generation=None):
    headers = {"Content-Type": "application/json"}
    if generation:
        headers["X-Cua-Driver-Generation"] = generation
    request = urllib.request.Request("http://127.0.0.1:6903" + path, data=json.dumps(body or {}).encode(), headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def close_driver_connection(connection):
    try:
        driver_request("/v1/connections/" + connection["connection_id"], method="DELETE", generation=connection["generation"])
    except urllib.error.HTTPError as error:
        # The private driver reaps idle connections after five minutes. A
        # missing connection is already revoked; other failures remain visible.
        if error.code != 404:
            raise


def exchange_driver(connection, envelope, *, observe):
    if connection is None:
        connection = driver_request("/v1/connections")
    try:
        result = driver_request("/v1/connections/" + connection["connection_id"] + "/exchange", envelope, generation=connection["generation"])
    except urllib.error.HTTPError as error:
        if error.code != 404 or not observe:
            raise
        # Only a fresh observation may reconnect automatically. Input actions
        # retain their old capture/generation and must never be replayed.
        connection = driver_request("/v1/connections")
        result = driver_request("/v1/connections/" + connection["connection_id"] + "/exchange", envelope, generation=connection["generation"])
    return connection, result


def stop_geometry(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    from sphinxsim import cli
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.visualization.preview import ConfigVisualizer
    from qtpy import QtCore, QtWidgets

    for directory in (ROOT, STATE.parent, WORK / "geometry", WORK / "cad", WORK / "preview"):
        directory.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["sphinxsim-native"])
    entry = ROOT / os.environ["SPHINXSIM_PROJECT_ENTRY"]
    if entry.is_symlink() or not entry.resolve(strict=True).is_relative_to(ROOT.resolve()):
        raise ValueError("Project entry must be inside the working project")
    host_mode = os.environ.get("SPHINXSIM_HOST_MODE", "local")
    if host_mode not in ("local", "external"):
        raise ValueError("Choose local or external host mode")
    from .local_host import LocalHost
    local_host = LocalHost(WORK, entry) if host_mode == "local" else None
    state = json.loads(STATE.read_text()) if STATE.exists() else {"version": 1}
    state.pop("controller", None)
    result_only = bool(os.environ.get("SPHINXSIM_RESULT_RUN_ID"))
    if result_only:
        state["readOnly"] = True
    automation_lock = threading.RLock()
    blocked = set(state.get("stoppedAutomation", []))
    active_automation = set()
    driver_serial = threading.Lock()
    display_mode = None
    base_font = app.font()
    runtime = cli._ShellPreviewRuntime()
    original_write = cli._write_validated_config
    hashes = {entry: hashlib.sha256(entry.read_bytes()).hexdigest()}
    current_job = {"runId": state.get("runId"), "state": "", "directory": None}
    build = {"process": None, "revision": 0, "shown": None, "error": None, "config": None}
    from sphinxsim_agent.desktop.modeling_session import ModelingSession
    from sphinxsim_agent.physics import check_config, editor_config_type
    # The upstream editor constructs this class before invoking its save hook.
    # Replace only this managed process's binding, keeping upstream untouched.
    cli.SimulationConfig = editor_config_type()
    modeling = ModelingSession(ROOT, entry)
    # The native renderer consumes files prepared by a child, never runs a
    # binding on the Qt event loop. Empty initial geometry is an explicit wait.
    ConfigVisualizer._try_build_geometries = lambda *_args, **_kwargs: build["shown"]
    original_mesh = ConfigVisualizer._load_shape_mesh

    def actual_mesh(visualizer, shape, directory, config):
        if shape.type.value not in ("bounding_box", "expanded_box"):
            if directory is None or not (directory / f"Shape{shape.name}.vtp").is_file():
                if directory is not None and shape.type.value == "complex_shape":
                    from sphinxsim_agent.export_vtp import composite_mesh
                    import numpy as np
                    import pyvista as pv
                    shapes = {s.name: s.model_dump(mode="json", exclude_none=True) for s in config.geometries.shapes}
                    points, faces = composite_mesh(shape.name, shapes, directory, config._infer_spatial_dim())
                    return pv.PolyData(points, np.column_stack((np.full(len(faces)//3, 3), faces.reshape(-1, 3))).ravel())
                raise ValueError(f"Geometry surface unavailable for {shape.name}; previous view retained")
            import pyvista as pv
            return pv.read(str(directory / f"Shape{shape.name}.vtp"))
        return original_mesh(visualizer, shape, directory, config)

    ConfigVisualizer._load_shape_mesh = actual_mesh

    def save(path, config):
        if state.get("readOnly") or state.get("intent") or current_job["state"] in ("queued", "preparing", "generating_particles", "relaxing", "running", "postprocessing", "canceling"):
            raise OSError("Desktop is read-only")
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes.get(path):
            raise OSError("File changed externally. Your unsaved edits are preserved; reload before saving.")
        try:
            check_config(config.model_dump(mode="json", exclude_none=True))
        except ValueError as exc:
            raise OSError(str(exc)) from exc
        result = original_write(path, config)
        hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    cli._write_validated_config = save
    # Create only the native editor/empty canvas before entering the event
    # loop. Native show_or_update also draws annotations synchronously, so it
    # must not be the startup gate for control/readiness or geometry work.
    from pyvistaqt import BackgroundPlotter
    runtime.plotter = BackgroundPlotter(title="SPHinXsim - Configuration Preview", show=True, shape=(1, 1), border=False, window_size=(1400, 800), update_app_icon=False, auto_update=False)
    runtime.plotter.app_window.showMaximized()
    runtime._using_background_plotter = True
    runtime._watch_preview_window_close()
    runtime._install_json_editor(SimulationConfig.model_validate_json(entry.read_text()), config_path=entry, with_particles=False)
    runtime._json_editor["tree"].setEnabled(not state.get("readOnly"))

    def schedule(config, *, resolved_config_path, with_particles, force=False):
        # Native particles/continue are deliberately supplied by the platform
        # execution queue; this launcher only builds cheap geometry in a child.
        payload = config.model_dump(mode="json", exclude_none=True)
        if not force and build["config"] is not None and build["error"] is None and payload == build["config"].model_dump(mode="json", exclude_none=True):
            return 0
        if build["process"] and build["process"].poll() is None:
            stop_geometry(build["process"])
        if build["revision"] > 0:
            if current_job["runId"] and current_job["state"] == "waiting" and not state.get("intent"):
                # Saving a changed native configuration invalidates retained
                # particles immediately, even before the next preview request.
                state["intent"] = {"id": str(uuid.uuid4()), "action": "invalidate", "runId": current_job["runId"]}
            current_job["directory"] = None
            current_job["runId"] = None
            state.pop("runId", None)
            atomic_json(STATE, state)
        build["revision"] += 1
        revision = build["revision"]
        directory = (WORK / "geometry") / str(uuid.uuid4())
        directory.mkdir(parents=True)
        (directory / "config.json").write_text(json.dumps(payload))
        log = (directory / "build.log").open("w")
        process = subprocess.Popen([sys.executable, "-m", "sphinxsim_agent.desktop.geometry", str(entry), str(directory)], stdout=log, stderr=subprocess.STDOUT)
        log.close()
        build.update(process=process, directory=directory, config=config, error=None)
        return 0

    runtime.show_or_update = schedule
    schedule(SimulationConfig.model_validate_json(entry.read_text()), resolved_config_path=entry, with_particles=False)
    app = QtWidgets.QApplication.instance()
    connection = None
    toolbar = runtime.plotter.app_window.addToolBar("Simulation status")
    toolbar.setObjectName("sphinxsim-status")
    label = QtWidgets.QLabel("Geometry is prepared asynchronously. Save with Ctrl+S.")
    toolbar.addWidget(label)

    def intent(action):
        if state.get("intent"):
            label.setText("Waiting for the control plane…")
            return state["intent"]
        if dirty() or state.get("readOnly"):
            raise ValueError("Save native changes first; historical results are read-only")
        if action == "continue" and current_job["state"] not in ("waiting", "preview_expired"):
            raise ValueError("Generate particles and wait before starting")
        if action == "particles" and current_job["state"] in ("queued", "preparing", "generating_particles", "relaxing", "running", "postprocessing", "canceling"):
            raise ValueError("An execution is already active")
        if action == "cancel" and not current_job["runId"]:
            raise ValueError("No execution to cancel")
        runtime._json_editor["tree"].setEnabled(False)
        state["intent"] = {"id": str(uuid.uuid4()), "action": action, "runId": current_job["runId"]}
        atomic_json(STATE, state)
        label.setText("Request saved; waiting for the control plane…")

    start = cancel_job = None
    generate = toolbar.addAction("Generate particles")
    generate.setEnabled(not state.get("readOnly"))
    def toolbar_intent(action):
        try:
            intent(action)
        except ValueError as error:
            label.setText(str(error))

    generate.triggered.connect(lambda: toolbar_intent("particles"))
    start = toolbar.addAction("Start simulation")
    start.triggered.connect(lambda: toolbar_intent("continue"))
    start.setEnabled(False)
    cancel_job = toolbar.addAction("Cancel simulation")
    cancel_job.triggered.connect(lambda: toolbar_intent("cancel"))
    cancel_job.setEnabled(False)


    presentation = None
    try:
        from sphinxsim_agent.desktop.presentation import Presentation
        presentation = Presentation(runtime, [
            ("particles", "生成粒子", "Generate particles", generate),
            ("continue", "开始 / 继续仿真", "Start / continue simulation", start),
            ("cancel", "取消计算", "Cancel simulation", cancel_job),
        ])
        # Actions now live in one Simulation menu; the toolbar only reports state.
        for existing in (generate, start, cancel_job):
            toolbar.removeAction(existing)
    except Exception as error:
        label.setText("Native presentation unavailable; original editor retained. " + str(error)[:200])

    def dirty():
        return bool(runtime._json_editor and runtime._json_editor["is_dirty"]())

    def application(command):
        from sphinxsim_agent.desktop.application import execute
        return execute(command, runtime=runtime, entry=entry, config_type=SimulationConfig,
                       save=save, schedule=schedule, intent=intent, current_job=current_job,
                       state=state, label=label, modeling=modeling)

    def apply_geometry(payload):
        normalized, _ = check_config(payload)
        config = SimulationConfig.model_validate(normalized)
        save(entry, config)
        runtime._json_editor["refresh"](config, entry, False)
        schedule(config, resolved_config_path=entry, with_particles=False, force=True)
        if not state.get("intent"):
            state["intent"] = {"id": str(uuid.uuid4()), "action": "save", "runId": current_job["runId"]}
            runtime._json_editor["tree"].setEnabled(False)
            atomic_json(STATE, state)

    def handle(command):
        nonlocal connection
        if command.get("version") != state["version"] and not (command.get("operation") == "status" and "version" not in command):
            raise ValueError("Stale control version")
        operation = command["operation"]
        if operation == "events":
            return {"intent": state.get("intent")}
        if operation == "accept":
            if state.get("intent", {}).get("id") != command.get("intentId"):
                if state.get("runId") == command.get("runId"):
                    return state
                raise ValueError("Stale intent")
            if command.get("error"):
                if modeling.job and modeling.job["state"] == "applied":
                    modeling.job["persistence"] = "failed"
                    modeling.persist()
                state.pop("intent", None)
                atomic_json(STATE, state)
                label.setText(str(command["error"])[:300])
                runtime._json_editor["tree"].setEnabled(True)
                generate.setEnabled(True)
                return state
            if state["intent"]["action"] == "save":
                state.pop("intent", None)
                if modeling.job and modeling.job["state"] == "applied":
                    modeling.job["persistence"] = "saved"
                    modeling.persist()
                atomic_json(STATE, state)
                runtime._json_editor["tree"].setEnabled(True)
                label.setText("Geometry saved to the experiment; preview rebuilding.")
                return state
            if state["intent"]["action"] == "invalidate":
                if modeling.job and modeling.job["state"] == "applied":
                    modeling.job["persistence"] = "saved"
                    modeling.persist()
                state.pop("intent", None)
                state["runId"] = ""
                current_job.update(runId="", state="", directory=None)
                atomic_json(STATE, state)
                runtime._json_editor["tree"].setEnabled(True)
                generate.setEnabled(True)
                start.setEnabled(False)
                cancel_job.setEnabled(False)
                label.setText("Configuration changed; previous particles released. Generate particles again.")
                return state
            current_job["state"] = "queued"
            state["runId"] = command["runId"]
            current_job["runId"] = command["runId"]
            state.pop("intent", None)
            atomic_json(STATE, state)
            runtime._json_editor["tree"].setEnabled(False)
            return state
        if operation == "status":
            return {**state, "geometryJob": modeling.job, "automationActive": bool(active_automation), "dirty": dirty(), "building": build["process"] is not None and build["process"].poll() is None, "applicationPhase": "error" if build["error"] else "building_geometry" if build["process"] is not None else "ready" if build.get("displayed") else "initializing", "error": build["error"]}
        if operation == "stop":
            with automation_lock:
                blocked.update(active_automation)
                if modeling.job and modeling.job["state"] == "building":
                    blocked.add(modeling.job["automationId"])
                if command.get("automationId"):
                    blocked.add(command["automationId"])
                if state.get("automationId"):
                    blocked.add(state["automationId"])
                state["stoppedAutomation"] = sorted(blocked)
                atomic_json(STATE, state)
                previous = connection
                connection = None
            if previous:
                close_driver_connection(previous)
            return {"stopped": True}
        if operation == "edit":
            if result_only:
                raise ValueError("Close the historical desktop before opening the current project editor")
            if not state.get("readOnly"):
                return {"readOnly": False}
            state.pop("readOnly", None)
            state.pop("runId", None)
            current_job.update(runId=None, state="", directory=None)
            atomic_json(STATE, state)
            config = SimulationConfig.model_validate_json(entry.read_bytes())
            runtime._json_editor["refresh"](config, entry, False)
            runtime._json_editor["tree"].setEnabled(True)
            generate.setEnabled(True)
            start.setEnabled(False)
            cancel_job.setEnabled(False)
            schedule(config, resolved_config_path=entry, with_particles=False, force=True)
            return {"readOnly": False}
        if operation == "result":
            if dirty() or state.get("intent"):
                raise ValueError("Save native edits before opening a result")
            state.update(runId=command["runId"], readOnly=True)
            current_job.update(runId=command["runId"], directory=None)
            atomic_json(STATE, state)
            return {"runId": command["runId"], "readOnly": True}
        if operation == "display":
            if active_automation or not driver_serial.acquire(blocking=False):
                return {"deferred": True}
            try:
                return resize_display(command)
            finally:
                driver_serial.release()
        if operation == "application":
            return application(command)
        if operation == "files":
            if state.get("readOnly"):
                raise ValueError("Historical result configuration cannot replace the project")
            return project_files()
        if operation not in ("observe", "action"):
            raise ValueError("Unsupported operation")
        with driver_serial:
            return visual_action(command)

    def resize_display(command):
        nonlocal connection, display_mode
        args = command.get("arguments") or {}
        width, height = args.get("width"), args.get("height")
        locale, theme = args.get("locale", "en"), args.get("themeMode", "dark")
        if locale not in ("zh", "zh-CN", "en", "en-US") or theme not in ("light", "dark"):
            raise ValueError("Invalid display locale or theme")
        scale = args.get("fontScale", 100)
        if type(width) is not int or type(height) is not int or not 1024 <= width <= 2560 or not 640 <= height <= 1440 or scale not in (100, 125, 150):
            raise ValueError("Invalid display settings")
        outputs = subprocess.run(["xrandr", "--query"], check=True, capture_output=True, text=True, timeout=3).stdout.splitlines()
        output = next(line.split()[0] for line in outputs if " connected" in line)
        mode = f"{width}x{height}"
        created = not any(line.strip().split()[0:1] == [mode] for line in outputs)
        if created:
            # Xvnc accepts RandR modes, but arbitrary CSS sizes are not predeclared.
            timing = [str((width + 32) * (height + 26) * 60 / 1_000_000),
                      str(width), str(width + 8), str(width + 16), str(width + 32),
                      str(height), str(height + 3), str(height + 6), str(height + 26)]
            subprocess.run(["xrandr", "--newmode", mode, *timing], check=True, capture_output=True, timeout=3)
            subprocess.run(["xrandr", "--addmode", output, mode], check=True, capture_output=True, timeout=3)
        subprocess.run(["xrandr", "--output", output, "--mode", mode], check=True, capture_output=True, timeout=3)
        if display_mode and display_mode != mode:
            subprocess.run(["xrandr", "--delmode", output, display_mode], capture_output=True, timeout=3)
            subprocess.run(["xrandr", "--rmmode", display_mode], capture_output=True, timeout=3)
        if created:
            display_mode = mode
        font = app.font()
        font.setPointSizeF(base_font.pointSizeF() * scale / 100)
        app.setFont(font)
        if presentation:
            presentation.apply(locale, theme)
        if connection:
            close_driver_connection(connection)
            connection = None
        return {"width": width, "height": height, "fontScale": scale, "observeAgain": True}
    def project_files():
        if dirty():
            raise ValueError("Save native editor before publishing")
        files = []
        total = 0
        for file in sorted(ROOT.rglob("*")):
            if file.is_symlink():
                raise ValueError("Project symlinks are forbidden")
            relative = file.relative_to(ROOT)
            if any(part.startswith(".") for part in relative.parts) or file.suffix.lower() not in (".json", ".stl", ".step", ".txt", ".md") or not file.is_file():
                continue
            size = file.stat().st_size
            total += size
            if size > 512 << 20 or total > 2 << 30 or len(files) >= 128:
                raise ValueError("Project exceeds budget")
            files.append({"path": str(relative), "bytes": size, "modifiedAtNs": file.stat().st_mtime_ns})
        return {"files": files, "savedConfigHash": hashlib.sha256(entry.read_bytes()).hexdigest()}
    def visual_action(command):
        nonlocal connection
        operation = command["operation"]
        if command.get("automationId") in blocked:
            raise ValueError("Automation stopped; end this turn")
        tool = "get_desktop_state" if operation == "observe" else command.get("tool")
        arguments = command.get("arguments") or {}
        if tool not in ALLOWED or any(key.startswith("_") or key in ("file_path", "image_path", "screenshot_out_file") for key in arguments):
            raise ValueError("Unsupported action")
        envelope = {"envelope_version": 1, "request_id": str(uuid.uuid4()), "operation": "call", "name": tool, "arguments": arguments, "deadline_unix_ms": int((time.time() + 20) * 1000)}
        connection, result = exchange_driver(connection, envelope, observe=operation == "observe")
        if command.get("automationId") in blocked:
            previous, connection = connection, None
            close_driver_connection(previous)
            raise ValueError("Automation stopped; end this turn")
        return result

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            future = Future()
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/control" or not 0 < size <= 1 << 20:
                    raise ValueError("Invalid request")
                command = json.loads(self.rfile.read(size))
                operation = command.get("operation")
                automation_id = command.get("automationId")
                if automation_id:
                    with automation_lock:
                        if automation_id in blocked:
                            raise ValueError("Automation stopped; end this turn")
                if operation in ("action", "application"):
                    if (operation == "action" or operation == "application" and command.get("command") != "screenshot") and (not isinstance(automation_id, str) or len(automation_id) != 64):
                        raise ValueError("Missing automation occurrence")
                    with automation_lock:
                        if automation_id in blocked:
                            raise ValueError("Automation stopped; end this turn")
                        if automation_id:
                            active_automation.add(automation_id)
                            state["automationId"] = automation_id
                if operation in ("observe", "action", "stop"):
                    # AT-SPI requests must leave the Qt loop free to answer.
                    value = handle(command)
                else:
                    PENDING.put_nowait((future, command))
                    try:
                        value = future.result(timeout=23)
                    except Exception:
                        future.cancel()
                        raise
                raw = json.dumps({"ok": True, "data": value}).encode()
            except Exception as error:
                raw = json.dumps({"ok": False, "error": str(error)[:300]}).encode()
            finally:
                if "automation_id" in locals() and automation_id:
                    with automation_lock:
                        active_automation.discard(automation_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_args):
            pass

    def show_view(visualizer, directory, bodies=None):
        camera = runtime.plotter.camera_position
        runtime.plotter.suppress_rendering = True
        try:
            previous_actors = dict(runtime.plotter.renderer.actors)
            runtime.plotter.clear()
            visualizer._populate_plotter(runtime.plotter, directory, bodies)
            runtime._install_annotation_hover(visualizer)
            if not build.get("displayed"):
                visualizer._configure_default_view(runtime.plotter, visualizer._spatial_dim())
                runtime.plotter.reset_camera(render=False)
            else:
                runtime.plotter.camera_position = camera
        except Exception:
            runtime.plotter.clear()
            for actor in previous_actors.values():
                runtime.plotter.renderer.add_actor(actor, reset_camera=False, render=False)
            runtime.plotter.camera_position = camera
            raise
        finally:
            runtime.plotter.suppress_rendering = False
        runtime.plotter.render()
        build["displayed"] = True

    def tick():
        if local_host:
            try:
                local_host.tick(state, handle)
            except Exception as error:
                label.setText("Latest native view unavailable; previous view retained. " + str(error)[:200])
        with automation_lock:
            modeling.tick(dirty=dirty(), frozen=bool(state.get("intent") or state.get("readOnly") or not runtime._json_editor["tree"].isEnabled()), blocked=blocked, apply=apply_geometry)
        try:
            future, command = PENDING.get_nowait()
        except queue.Empty:
            pass
        else:
            if future.set_running_or_notify_cancel():
                try:
                    if command.get("automationId") in blocked:
                        raise ValueError("Automation stopped; end this turn")
                    future.set_result(handle(command))
                except Exception as error:
                    future.set_exception(error)
        view_path = (WORK / "preview/view.json")
        if view_path.exists():
            try:
                view = json.loads(view_path.read_text())
                if view.get("runId") == current_job["runId"]:
                    current_job["state"] = view["state"]
                    label.setText(view["state"] + (" • " + str(view["error"]) if view.get("error") else ""))
                    busy = bool(state.get("readOnly")) or bool(state.get("intent")) or (bool(view.get("formal")) and view["state"] == "waiting") or view["state"] in ("queued", "preparing", "generating_particles", "relaxing", "running", "postprocessing", "canceling")
                    runtime._json_editor["tree"].setEnabled(not busy)
                    generate.setEnabled(not busy and not state.get("intent"))
                    start.setEnabled(not state.get("readOnly") and view["state"] in ("waiting", "preview_expired") and not state.get("intent"))
                    cancel_job.setEnabled(not state.get("readOnly") and (busy or view["state"] == "waiting"))
                    directory = view.get("directory")
                    if directory and directory != current_job["directory"]:
                        target = (WORK / "preview") / directory
                        config = SimulationConfig.model_validate_json((target / "effective.json").read_text())
                        bodies = {body["name"]: target / (body["name"] + ".vtp") for kind in ("fluid_bodies", "continuum_bodies", "solid_bodies") for body in config.model_dump().get(kind, []) if (target / (body["name"] + ".vtp")).exists()}
                        if state.get("readOnly"):
                            runtime._json_editor["refresh"](config, entry, False)
                            runtime._json_editor["tree"].setEnabled(False)
                        visualizer = ConfigVisualizer(config, cli.PROJECT_ROOT, config_path=entry, off_screen=False)
                        build["shown"] = target
                        show_view(visualizer, target, bodies)
                        current_job["directory"] = directory
            except Exception as error:
                label.setText("Latest native view unavailable; previous view retained. Retry from the desktop. " + str(error)[:200])
        process = build["process"]
        if process is not None and process.poll() is not None:
            build["process"] = None
            if process.returncode == 0:
                build["shown"] = build["directory"] / "output" / "output"
                if not build["shown"].is_dir():
                    build["shown"] = build["directory"] / "output"
                # A late geometry build must not replace newer particle output.
                if current_job["directory"]:
                    return
                # Rendering a completed build must not discard unapplied edits.
                config = build["config"]
                visualizer = ConfigVisualizer(config, cli.PROJECT_ROOT, config_path=entry, off_screen=False)
                try:
                    show_view(visualizer, build["shown"])
                    label.setText("Geometry ready. Save with Ctrl+S, then generate particles.")
                except Exception as error:
                    build["error"] = str(error)[:2000]
                    runtime._json_editor["status"].setText("Native geometry display failed. " + build["error"])
            else:
                build["error"] = (build["directory"] / "build.log").read_text()[-2000:]
                runtime._json_editor["status"].setText("Geometry build failed; previous view retained. " + build["error"])

        if presentation:
            presentation.translate_status(label)
            presentation.translate_status(runtime._json_editor["status"])

    server = ThreadingHTTPServer(("127.0.0.1", 6902), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    modeling.cancel("interrupted")
    if build["process"] and build["process"].poll() is None:
        stop_geometry(build["process"])


if __name__ == "__main__":
    main()
