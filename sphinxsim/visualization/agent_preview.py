"""Independent Preview window observing durable Agent runs, never running them."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

from sphinxsim.results import ResultCatalog


@dataclass(frozen=True)
class ObservedRun:
    config_path: Path
    output_dir: Path
    label: str
    status: str


class WorkflowPreviewSource:
    """Read atomically persisted workflow/attempt files without loading an LLM."""

    def __init__(self, workspace, workflow_id):
        if not workflow_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in workflow_id):
            raise ValueError("invalid workflow identifier")
        self.workspace = Path(workspace).resolve()
        self.workflow_id = workflow_id
        self.selection = "live"

    def poll(self):
        workflow_path = self.workspace / "store/workflows" / self.workflow_id / "workflow.json"
        if not workflow_path.exists():
            return None
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        status = workflow["status"]
        worker_path = self.workspace / "execution" / self.workflow_id / "worker.json"
        if status == "running" and worker_path.exists():
            from sphinxsim.services.process_control import alive
            worker = json.loads(worker_path.read_text(encoding="utf-8"))
            status = worker.get("state", status)
            if status in {"starting", "running", "cancelling"} and not alive(worker):
                status = "interrupted"
        if self.selection == "best" or self.selection.startswith("candidate:"):
            iterations = workflow.get("iterations", [])
            if not iterations:
                return None
            if self.selection == "best":
                best = min(iterations, key=lambda item: item["absolute_error"])
            else:
                selected = int(self.selection.split(":", 1)[1])
                best = next((item for item in iterations if item["iteration"] == selected), None)
                if best is None:
                    return None
            run_dir = self.workspace / "store" / best["task_id"] / "runs" / best["run_id"]
            return ObservedRun(Path(best["case_path"]), run_dir,
                f"{'Best candidate' if self.selection == 'best' else 'Candidate'} {best['iteration']}: {best['parameter_value']:g}", status)
        attempts = []
        for path in (self.workspace / "execution" / self.workflow_id).glob("attempts/*/attempt.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("operation") == "run" and record.get("output_dir"):
                attempts.append(record)
        if not attempts:
            return None
        current = max(attempts, key=lambda item: item["started_at"])
        config = current.get("config_path")
        if not config:
            command = current["command"]
            config = command[command.index("run") + 1]
        active = current.get("status") in {"starting", "running"}
        iteration = len(workflow.get("iterations", [])) + int(active)
        return ObservedRun(Path(config), Path(current["output_dir"]),
            f"Candidate {max(1, iteration)}: {Path(config).stem}", status)


class AgentPreviewObserver:
    """Update one Plotter on the Qt main thread and preserve its camera."""

    def __init__(self, source, plotter, project_root):
        self.source = source
        self.plotter = plotter
        self.project_root = Path(project_root)
        self.catalog = ResultCatalog()
        self.versions = {}
        self.scene_key = None
        self.visualizer = None

    def tick(self):
        from sphinxsim.config.schemas import SimulationConfig
        from sphinxsim.visualization.preview import ConfigVisualizer, _body_colour
        from sphinxsim.visualization.live_particles import refresh_particle_actors

        run = self.source.poll()
        if run is None:
            self.plotter.add_text("Waiting for the first Agent run...", name="agent-status",
                                  position="lower_left", color="white", font_size=10)
            return
        root = run.output_dir / "output" if (run.output_dir / "output").is_dir() else run.output_dir
        shapes = tuple(sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in root.glob("Shape*.vtp")))
        key = (str(run.config_path), str(run.output_dir), shapes)
        if key != self.scene_key:
            config = SimulationConfig.model_validate_json(run.config_path.read_text(encoding="utf-8"))
            camera = self.plotter.camera_position if self.visualizer is not None else None
            self.visualizer = ConfigVisualizer(config, self.project_root, config_path=run.config_path)
            self.visualizer.populate_existing_run(self.plotter, run.output_dir)
            if camera is not None:
                self.plotter.camera_position = camera
            self.versions = {}
            self.scene_key = key
        config = self.visualizer.config
        bodies = config.fluid_bodies + config.continuum_bodies + config.solid_bodies
        colours = {body.name: _body_colour(body.name, config) for body in bodies}
        self.visualizer._select_subplot(self.plotter, 0)
        frames = refresh_particle_actors(self.plotter, run.output_dir, colours, self.versions, self.catalog)
        for body in config.fluid_bodies + config.continuum_bodies:
            if body.name in self.versions:
                self.plotter.remove_actor(f"preview-shape-{body.name}", reset_camera=False)
        time_text = ", ".join(f"{body}: {frame.time:.6g} s" for body, frame in frames.items() if not frame.generation)
        self.plotter.add_text(f"{run.label} | {run.status}\n{time_text or 'Waiting for solver frames'}",
                             name="agent-status", position="lower_left", color="white", font_size=10)
        self.plotter.render()


def launch_agent_preview(project_root, workspace, workflow_id):
    """Launch at most one independent viewer per workflow; return PID metadata."""
    from sphinxsim.services.process_control import alive, identity, workspace_lock
    from sphinxsim.agent.store import AgentTaskStore

    root = Path(project_root).resolve()
    workspace = Path(workspace).resolve()
    import importlib.util
    if any(importlib.util.find_spec(name) is None for name in ("pyvistaqt", "PySide6")):
        raise RuntimeError("Preview requires pyvistaqt and PySide6 in the active Python environment")
    WorkflowPreviewSource(workspace, workflow_id)  # validate identifier
    directory = workspace / "execution" / workflow_id
    directory.mkdir(parents=True, exist_ok=True)
    with workspace_lock(directory / "preview.lock"):
        record_path = directory / "preview.json"
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if alive(record):
                return record
        command = [sys.executable, "-m", "sphinxsim.visualization.agent_preview",
                   str(root), str(workspace), workflow_id]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        with (directory / "preview.log").open("ab") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt")
        record = dict(identity(process.pid), workflow_id=workflow_id)
        AgentTaskStore._atomic_write(record_path, json.dumps(record, indent=2))
        return record


def main():
    parser = argparse.ArgumentParser(description="Observe existing Agent runs in Preview.")
    parser.add_argument("project_root")
    parser.add_argument("workspace")
    parser.add_argument("workflow_id")
    args = parser.parse_args()
    from qtpy import QtWidgets, QtCore
    from pyvistaqt import BackgroundPlotter

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    plotter = BackgroundPlotter(title=f"Agent Preview — {args.workflow_id}", shape=(1, 2),
                                border=False, window_size=(1400, 800), auto_update=False)
    source = WorkflowPreviewSource(args.workspace, args.workflow_id)
    observer = AgentPreviewObserver(source, plotter, args.project_root)
    menu = plotter.app_window.menuBar().addMenu("Agent results")
    for label, selection in (("Follow current run", "live"), ("Show best completed candidate", "best")):
        action = menu.addAction(label)
        action.triggered.connect(lambda checked=False, choice=selection: setattr(source, "selection", choice))
    def show_candidate(index):
        source.selection = "candidate:" + str(index)
    candidate_menu = menu.addMenu("Completed candidates")
    def rebuild_candidates():
        candidate_menu.clear()
        path = source.workspace / "store/workflows" / source.workflow_id / "workflow.json"
        if not path.exists():
            return
        for item in json.loads(path.read_text(encoding="utf-8")).get("iterations", []):
            action = candidate_menu.addAction(f"Candidate {item['iteration']}: {item['parameter_value']:g}")
            action.triggered.connect(lambda checked=False, index=item["iteration"]: show_candidate(index))
    candidate_menu.aboutToShow.connect(rebuild_candidates)
    timer = QtCore.QTimer()
    def refresh():
        try:
            observer.tick()
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Preview refresh deferred: {exc}", flush=True)
    timer.timeout.connect(refresh)
    timer.start(1000)
    plotter.app_window.signal_close.connect(timer.stop)
    plotter.app_window.signal_close.connect(app.quit)
    refresh()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
