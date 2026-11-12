"""Qt workbench: Preview left, peer JSON Editor and Chat tabs on the right."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Empty
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from uuid import uuid4

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWebEngineWidgets import QWebEngineView

from sphinxsim.ui.chatbot.application import _CHAINLIT_CONFIG, _chainlit_command
from sphinxsim.ui.json_editor import JsonPropertyEditor
from sphinxsim.ui.workbench.bridge import WorkbenchBridge
from sphinxsim.ui.workbench.document import WorkbenchDocument
from sphinxsim.ui.workbench.viewport import PreviewViewport
from sphinxsim.visualization.agent_preview import AgentPreviewObserver, ObservedRun, WorkflowPreviewSource


class ChainlitProcess:
    """Own only the chat server; durable Agent workers keep their independent lifetime."""
    def __init__(self, root, workspace, bridge):
        self.root, self.workspace, self.bridge = root, workspace, bridge
        self.app_root = tempfile.TemporaryDirectory(prefix="sphinxsim-workbench-")
        app = Path(self.app_root.name)
        (app / ".chainlit").mkdir()
        (app / ".chainlit/config.toml").write_text(
            _CHAINLIT_CONFIG.replace('default_theme = "light"', 'default_theme = "dark"\nlanguage = "en-US"'), encoding="utf-8")
        (app / "chainlit.md").write_text("", encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        environment = os.environ.copy()
        environment.pop("DEBUG", None)
        environment.update(CHAINLIT_APP_ROOT=self.app_root.name,
                           SPHINXSIM_AGENT_PROJECT_ROOT=str(self.root),
                           SPHINXSIM_AGENT_WORKSPACE=str(self.workspace),
                           SPHINXSIM_WORKBENCH_URL=self.bridge.url,
                           SPHINXSIM_WORKBENCH_TOKEN=self.bridge.token,
                           PYTHONPATH=str(self.root) + os.pathsep + environment.get("PYTHONPATH", ""),
                           PYTHONIOENCODING="utf-8")
        with (self.workspace / "chat.log").open("ab") as log:
            self.process = subprocess.Popen(
                _chainlit_command(host="127.0.0.1", port=self.port, headless=True, watch=False),
                cwd=self.root, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.app_root.cleanup()


class FixedRunSource:
    def __init__(self, run):
        self.run = run

    def poll(self):
        return self.run


class WorkbenchWindow(QtWidgets.QMainWindow):
    def __init__(self, project_root, workspace, config_path=None, *, start_chat=True):
        super().__init__()
        self.root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if config_path is None:
            config_path = self.workspace / "case.json"
            if not config_path.exists():
                shutil.copyfile(self.root / "tests/test_simulation/test_2d_simulation/data/column_collapse.json", config_path)
        self.document = WorkbenchDocument(config_path)
        self.bridge = WorkbenchBridge(self.document)
        self.chat_server = ChainlitProcess(self.root, self.workspace, self.bridge) if start_chat else None
        self.observer = None
        self.preview_job = None
        self.run_job = None
        self.preview_state = None
        self.preview_dimension = None
        self.preview_requested = False
        self.proposal_signature = None
        self.chat_loaded = False
        self._closing = False
        self.setWindowTitle("SPHinXsim Workbench")
        available = self.screen().availableGeometry()
        self.resize(min(1560, int(available.width() * 0.94)), min(1000, int(available.height() * 0.92)))
        self._build_ui()
        self._load_editor()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(750)
        if self.chat_server:
            self.chat_server.start()
        QtCore.QTimer.singleShot(200, self.refresh_preview)

    def _build_ui(self):
        toolbar = self.addToolBar("Workbench")
        toolbar.setMovable(False)
        for label, callback in (("Open JSON", self.open_file), ("Save + Preview", self.save),
                                ("Validate", self.validate), ("Preview draft", self.refresh_preview),
                                ("Run saved case", self.run_saved), ("Stop case run", self.stop_run),
                                ("Reconnect chat", self.reconnect_chat)):
            action = toolbar.addAction(label)
            action.triggered.connect(callback)
        save_action = QtGui.QAction(self)
        save_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save)
        self.addAction(save_action)
        self.particles = QtWidgets.QCheckBox("Initial particles")
        toolbar.addWidget(self.particles)

        self.horizontal_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.horizontal_split.setChildrenCollapsible(False)
        self.setCentralWidget(self.horizontal_split)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(left)
        self.display_label = QtWidgets.QLabel("Configuration preview")
        layout.addWidget(self.display_label)
        self.viewport = PreviewViewport(left)
        self.plotter = self.viewport.plotter
        layout.addWidget(self.viewport, 1)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Agent results:"))
        self.selection = QtWidgets.QComboBox()
        self.selection.addItem("Follow current run", "live")
        self.selection.addItem("Best completed candidate", "best")
        self.selection.currentIndexChanged.connect(self._select_candidate)
        controls.addWidget(self.selection)
        self.workflow_input = QtWidgets.QLineEdit()
        self.workflow_input.setPlaceholderText("Workflow ID to attach")
        controls.addWidget(self.workflow_input)
        attach = QtWidgets.QPushButton("Attach")
        attach.clicked.connect(lambda: self.observe_workflow(self.workflow_input.text().strip()))
        controls.addWidget(attach)
        layout.addLayout(controls)
        self.horizontal_split.addWidget(left)

        self.side_tabs = QtWidgets.QTabWidget()
        self.side_tabs.setObjectName("workbench-sidebar")
        self.side_tabs.setDocumentMode(True)
        self.side_tabs.setMinimumWidth(420)
        self.side_tabs.setStyleSheet("QTabBar::tab { padding: 8px 12px; }"
                                    "QTabBar::tab:selected { border-bottom: 2px solid #5794cf; }")
        self.horizontal_split.addWidget(self.side_tabs)
        editor_panel = self.editor_panel = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor_panel)
        self.file_label = QtWidgets.QLabel()
        self.file_label.setMinimumWidth(0)
        self.file_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        editor_layout.addWidget(self.file_label)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.editor = JsonPropertyEditor()
        self.editor.setObjectName("json-editor")
        self.editor.status.hide()  # The workbench shares one editor status line.
        self.editor.save_shortcut.setEnabled(False)  # Ctrl+S belongs to the workbench action.
        self.editor.saveRequested.connect(self.save)
        self.editor.reloadRequested.connect(self.reload_file)
        self.editor.textChanged.connect(self._edited)
        self.editor.tree.setColumnWidth(0, 190)
        self.tabs.addTab(self.editor, "Properties")
        self.diff = QtWidgets.QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(QtGui.QFont("Consolas", 10))
        self.tabs.addTab(self.diff, "LLM diff")
        self.run_config = QtWidgets.QPlainTextEdit()
        self.run_config.setReadOnly(True)
        self.run_config.setFont(self.diff.font())
        run_tab = self.tabs.addTab(self.run_config, "Run config")
        self.tabs.setTabToolTip(run_tab, "Displayed run configuration (read-only)")
        editor_layout.addWidget(self.tabs)
        proposal_buttons = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton("Apply proposal to draft")
        self.apply_button.setEnabled(False)
        self.apply_button.hide()
        self.apply_button.clicked.connect(self.apply_proposal)
        reject = self.discard_button = QtWidgets.QPushButton("Discard proposal")
        reject.hide()
        reject.clicked.connect(self.discard_proposal)
        proposal_buttons.addWidget(self.apply_button, 1)
        proposal_buttons.addWidget(reject)
        editor_layout.addLayout(proposal_buttons)
        self.editor_status = QtWidgets.QLabel("Ctrl+S validates, saves and refreshes Preview.")
        self.editor_status.setWordWrap(True)
        editor_layout.addWidget(self.editor_status)
        self.side_tabs.addTab(editor_panel, "JSON Editor")

        chat_panel = self.chat_panel = QtWidgets.QWidget()
        chat_layout = QtWidgets.QVBoxLayout(chat_panel)
        chat_layout.addWidget(QtWidgets.QLabel("LLM chat · /config <change> proposes a JSON edit"))
        self.web = QWebEngineView()
        self.web.page().profile().setHttpAcceptLanguage("en-US,en;q=0.9")
        self.web.setMinimumSize(320, 240)
        self.web.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.web.setObjectName("chainlit-chat")
        self.web.setHtml("<h2>SPHinXsim</h2><p>Starting the local chat server…</p>")
        chat_layout.addWidget(self.web, 1)
        self.side_tabs.addTab(chat_panel, "Chat")
        self.side_tabs.currentChanged.connect(self._sidebar_changed)
        self.horizontal_split.setStretchFactor(0, 2)
        self.horizontal_split.setStretchFactor(1, 1)
        self.horizontal_split.setSizes([950, 550])
        self.statusBar().showMessage("Preparing workbench…")

    def _sidebar_changed(self, index):
        if self.side_tabs.widget(index) is self.chat_panel:
            self.web.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        elif self.tabs.currentWidget() is self.editor:
            self.editor.tree.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def _update_sidebar_title(self):
        proposal = self.document.pending_proposal()
        dirty = self.document.snapshot()["dirty"]
        self.side_tabs.setTabText(0, "JSON Editor" + (" •" if proposal else " *" if dirty else ""))
        self.side_tabs.setTabToolTip(0, "An LLM proposal is ready for review." if proposal
                                     else "Unsaved JSON draft." if dirty else str(self.document.path))

    def _load_editor(self):
        self.editor.set_text(self.document.text, config_path=self.document.path, saved_text=self.document.saved_text)
        self._edited()

    def _edited(self, *_):
        self.document.set_text(self.editor.text())
        snapshot = self.document.snapshot()
        self.file_label.setText(self.document.path.name + ("  • Unsaved draft" if snapshot["dirty"] else "  • Saved"))
        self.file_label.setToolTip(str(self.document.path))
        self.editor_status.setText(self.editor.status.text())
        self._update_sidebar_title()

    def _error(self, exc):
        message = str(exc)
        self.editor_status.setToolTip(message)
        self.editor_status.setText(" ".join(message.splitlines()[:2])[:220])
        self.statusBar().showMessage(message.splitlines()[0][:400] if message else "Operation failed")

    def validate(self):
        try:
            self.editor.validate_config()
            self.document.validate()
            self.editor_status.setText("Valid configuration.")
            return True
        except (ValueError, OSError) as exc:
            self._error(exc)
            return False

    def save(self):
        try:
            self.editor.validate_config()
            self.document.save()
            self.editor.mark_saved()
            self._edited()
            self.editor_status.setText("Saved. Rebuilding Preview…")
            self.refresh_preview()
        except (ValueError, OSError) as exc:
            self._error(exc)

    def _confirm_discard(self):
        if not self.document.snapshot()["dirty"]:
            return True
        return QtWidgets.QMessageBox.question(self, "Unsaved JSON", "Discard unsaved draft changes?",
                    QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel,
                    QtWidgets.QMessageBox.StandardButton.Cancel) == QtWidgets.QMessageBox.StandardButton.Discard

    def open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open simulation JSON", str(self.root), "JSON (*.json)")
        if path:
            try:
                self.document.load(path)
                self.discard_proposal()
                self._load_editor()
                self.side_tabs.setCurrentWidget(self.editor_panel)
                self.refresh_preview()
            except (ValueError, OSError) as exc:
                self._error(exc)

    def reload_file(self):
        if not self._confirm_discard():
            return
        try:
            self.document.load(self.document.path)
            self.discard_proposal()
            self._load_editor()
            self.refresh_preview()
        except (ValueError, OSError) as exc:
            self._error(exc)

    def apply_proposal(self):
        try:
            self.editor.set_text(self.document.apply_proposal(), saved_text=self.document.saved_text, remember=True)
            self.discard_proposal()
            self.side_tabs.setCurrentWidget(self.editor_panel)
            self.tabs.setCurrentWidget(self.editor)
            self.editor_status.setText("Applied to draft. Save + Preview to persist and refresh.")
        except ValueError as exc:
            self._error(exc)

    def discard_proposal(self):
        with self.document.lock:
            self.document.proposal = None
        self.proposal_signature = None
        self.diff.clear()
        self.apply_button.setEnabled(False)
        self.apply_button.hide()
        self.discard_button.hide()
        self._update_sidebar_title()

    def refresh_preview(self):
        if self.preview_job is not None and self.preview_job.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._error("Preview is being prepared; wait before refreshing again.")
            return
        if not self.validate():
            return
        snapshot = self.document.snapshot()
        output = self.workspace / "previews" / uuid4().hex
        output.mkdir(parents=True)
        draft = output / "draft.json"
        draft.write_text(snapshot["text"], encoding="utf-8")
        self.preview_state = (snapshot, output)
        self.preview_requested = True
        process = self.preview_job = QtCore.QProcess(self)
        self._configure_native_process(process, output)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        process.setStandardOutputFile(str(output / "preview.log"))
        args = ["-m", "sphinxsim.ui.workbench.preview_worker", str(draft), snapshot["config_path"], str(output)]
        if self.particles.isChecked():
            args.append("--particles")
        process.finished.connect(self._preview_finished)
        process.errorOccurred.connect(lambda error: self._error("Preview process could not start: " + process.errorString()))
        self.statusBar().showMessage("Building Preview in a separate process…")
        process.start(sys.executable, args)

    def _preview_finished(self, code, status):
        from sphinxsim.config.schemas import SimulationConfig
        from sphinxsim.visualization.preview import ConfigVisualizer, _body_colour
        from sphinxsim.visualization.live_particles import refresh_particle_actors
        from sphinxsim.results import ResultCatalog
        snapshot, output = self.preview_state
        if not self.preview_requested:
            return
        if code != 0:
            self._error(f"Preview failed. See {output / 'preview.log'}")
            return
        if self.document.snapshot()["revision"] != snapshot["revision"]:
            self._error("The draft changed while preparing Preview. Refresh again to display it.")
            return
        try:
            config = SimulationConfig.model_validate_json(snapshot["text"])
            visualizer = ConfigVisualizer(config, self.root, config_path=Path(snapshot["config_path"]))
            visualizer._shape_bounds_cache = json.loads((output / "ready.json").read_text(encoding="utf-8"))["bounds"]
            dimension = visualizer._spatial_dim()
            camera = (self.plotter.camera_position if self.observer is None
                      and self.preview_dimension == dimension and self.plotter.renderer.actors else None)
            visualizer.populate_existing_run(self.plotter, output)
            bodies = config.fluid_bodies + config.continuum_bodies + config.solid_bodies
            refresh_particle_actors(self.plotter, output, {b.name: _body_colour(b.name, config) for b in bodies}, {}, ResultCatalog())
            if camera is not None:
                self.plotter.camera_position = camera
            self.observer = None
            self.preview_dimension = dimension
            self._set_display("Draft preview • " + Path(snapshot["config_path"]).name)
            self.plotter.render()
            self.statusBar().showMessage("Preview ready.")
        except (ValueError, OSError, RuntimeError) as exc:
            self._error(exc)

    def _set_display(self, label):
        self.display_label.setText(label)
        with self.document.lock:
            self.document.display = label

    def _configure_native_process(self, process, output):
        # Native constructors may touch ./output before resetOutputRoot(). Keep
        # both preview preparation and solver startup inside their isolated run.
        process.setWorkingDirectory(str(output))
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONPATH", str(self.root) + os.pathsep + environment.value("PYTHONPATH"))
        process.setProcessEnvironment(environment)

    def observe_workflow(self, workflow_id):
        try:
            source = WorkflowPreviewSource(self.workspace, workflow_id)
            if not (self.workspace / "store/workflows" / workflow_id / "workflow.json").is_file():
                raise ValueError("Workflow not found in this workbench workspace.")
            self.observer = AgentPreviewObserver(source, self.plotter, self.root)
            self.preview_requested = False
            self.selection.setCurrentIndex(0)
            self.plotter.clear()
            self.run_config.clear()
            self.workflow_input.setText(workflow_id)
            self._set_display("Following Agent " + workflow_id)
        except ValueError as exc:
            self._error(exc)

    def _select_candidate(self):
        if self.observer and isinstance(self.observer.source, WorkflowPreviewSource):
            self.observer.source.selection = self.selection.currentData()

    def run_saved(self):
        if self.run_job is not None and self.run_job.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._error("A case run is already active.")
            return
        if self.document.snapshot()["dirty"]:
            self._error("Save the JSON draft before running.")
            return
        if not self.validate():
            return
        # The run receives an immutable snapshot; subsequent editor changes never alter it.
        # Avoid competing with an Agent worker in the same workspace.
        from sphinxsim.services.process_control import alive
        for record in (self.workspace / "execution").glob("*/worker.json"):
            if alive(json.loads(record.read_text(encoding="utf-8"))):
                self._error("An Agent worker is active. Cancel it before starting a manual case run.")
                return
        output = self.workspace / "manual_runs" / uuid4().hex
        output.mkdir(parents=True)
        payload = json.loads(self.document.text)
        from sphinxsim.ui.workbench.document import rebase_asset_paths
        case = output / "case.json"
        case.write_text(json.dumps(rebase_asset_paths(payload, self.document.path.parent), indent=2), encoding="utf-8")
        process = self.run_job = QtCore.QProcess(self)
        self._configure_native_process(process, output)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        process.setStandardOutputFile(str(output / "solver.log"))
        source = FixedRunSource(ObservedRun(case, output, "Saved case run", "running"))
        self.observer = AgentPreviewObserver(source, self.plotter, self.root)
        self.preview_requested = False
        with self.document.lock:
            self.document.manual_run_active = True
        process.finished.connect(lambda code, status: self._run_finished(source, code, output))
        def run_error(error):
            if error == QtCore.QProcess.ProcessError.FailedToStart:
                with self.document.lock:
                    self.document.manual_run_active = False
            self._error("Solver process: " + process.errorString())
        process.errorOccurred.connect(run_error)
        process.start(sys.executable, ["-m", "sphinxsim.ui.workbench.run_worker", str(self.root), str(case), str(output)])

    def _run_finished(self, source, code, output):
        with self.document.lock:
            self.document.manual_run_active = False
        run = source.run
        source.run = ObservedRun(run.config_path, run.output_dir, run.label, "completed" if code == 0 else "failed")
        self.statusBar().showMessage(f"Case run {'completed' if code == 0 else 'failed'} • {output / 'solver.log'}")

    def stop_run(self):
        if self.run_job is not None and self.run_job.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.run_job.kill()
            self.run_job.waitForFinished(3000)

    def reconnect_chat(self):
        if self.chat_server:
            self.chat_server.start()
            self.chat_loaded = False
            self.side_tabs.setCurrentWidget(self.chat_panel)

    def _tick(self):
        proposal = self.document.pending_proposal()
        if proposal and proposal != self.proposal_signature:
            self.proposal_signature = proposal
            self.diff.setPlainText(proposal["diff"] or "The proposed configuration is unchanged.")
            self.apply_button.setEnabled(True)
            self.apply_button.show()
            self.discard_button.show()
            self.tabs.setCurrentWidget(self.diff)
            self.editor_status.setText("LLM proposal ready. Review the diff before applying.")
            self._update_sidebar_title()
            self.statusBar().showMessage("LLM proposal ready. Open JSON Editor > LLM diff to review.")
        try:
            while True:
                action, workflow_id = self.document.actions.get_nowait()
                if action == "observe":
                    if self.run_job and self.run_job.state() != QtCore.QProcess.ProcessState.NotRunning:
                        self._error("Stop the manual case run before following an Agent.")
                    else:
                        self.observe_workflow(workflow_id)
        except Empty:
            pass
        if self.observer:
            try:
                self.observer.tick()
                run = self.observer.source.poll()
                if run:
                    self._set_display(f"{run.label} • {run.status} • {run.config_path.name}")
                    text = run.config_path.read_text(encoding="utf-8")
                    if self.run_config.toPlainText() != text:
                        self.run_config.setPlainText(text)
                if isinstance(self.observer.source, WorkflowPreviewSource):
                    path = self.workspace / "store/workflows" / self.observer.source.workflow_id / "workflow.json"
                    items = json.loads(path.read_text(encoding="utf-8")).get("iterations", [])
                    if self.selection.count() != len(items) + 2:
                        selection = self.selection.currentData()
                        self.selection.blockSignals(True)
                        while self.selection.count() > 2:
                            self.selection.removeItem(2)
                        for item in items:
                            self.selection.addItem(f"Candidate {item['iteration']}: {item['parameter_value']:g}", f"candidate:{item['iteration']}")
                        self.selection.setCurrentIndex(max(0, self.selection.findData(selection)))
                        self.selection.blockSignals(False)
            except (OSError, ValueError, RuntimeError) as exc:
                self.statusBar().showMessage("Waiting for completed result files: " + str(exc))
        if self.chat_server and not self.chat_loaded:
            if self.chat_server.process.poll() is not None:
                self.statusBar().showMessage(f"Chat stopped. Reconnect chat; diagnostics: {self.workspace / 'chat.log'}")
                return
            try:
                with urlopen(self.chat_server.url + "/health", timeout=0.1) as response:
                    ready = response.status == 200
                if ready:
                    self.web.load(QtCore.QUrl(self.chat_server.url))
                    self.chat_loaded = True
            except OSError:
                pass

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        if self.run_job and self.run_job.state() != QtCore.QProcess.ProcessState.NotRunning:
            answer = QtWidgets.QMessageBox.question(self, "Running case", "Stop the manual case run and close?",
                        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.timer.stop()
        self.stop_run()
        if self.preview_job and self.preview_job.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.preview_job.kill()
            self.preview_job.waitForFinished(3000)
        if self.chat_server:
            self.chat_server.close()
        self.bridge.close()
        self.viewport.close()
        event.accept()


def launch_workbench(project_root, workspace, config_path=None):
    # Qt WebEngine and VTK share OpenGL resources in the desktop process.
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = WorkbenchWindow(project_root, workspace, config_path)
    window.show()
    return app.exec()
