"""Frozen-input protection and native lifecycle tests, collected by pytest."""
import json
import os
from pathlib import Path
import time
import uuid

import pytest

from sphinxsim.operations import Operations
from sphinxsim.runtime.jobs import TERMINAL
from .test_configuration import j2_configuration


def require_native():
    pytest.importorskip("vtkmodules")
    from sphinxsim.bindings.loader import load_sphinxsys_core_nd
    try:
        return load_sphinxsys_core_nd(2)
    except ImportError as exc:
        if os.environ.get("SPHINXSIM_REQUIRE_NATIVE_TESTS") == "1":
            pytest.fail(str(exc))
        pytest.skip("Native 2D binding is not installed; native CI requires this test")


def configuration():
    source = Path(__file__).parents[1] / "test_simulation/test_2d_simulation/data/dambreak.json"
    config = json.loads(source.read_text())
    config.pop("characteristic_dimensions", None)
    config["geometries"]["global_resolution"]["particle_spacing"] = .1
    config["particle_generation"]["settings"]["relaxation_parameters"]["total_iterations"] = 1
    config["solver_parameters"].update(end_time=.002, output_interval=.001)
    return config


def prepare(tmp_path, config):
    case = tmp_path / "case"
    case.mkdir()
    entry = case / "config.json"
    entry.write_text(json.dumps(config))
    return Operations(tmp_path), entry


def wait(operations, reference, states):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = operations.call("job_status", {"reference": reference})
        if state["state"] in states or state["state"] in TERMINAL:
            assert state["state"] in states, (state, operations.call("job_logs", {"reference": reference}))
            return state
        time.sleep(.1)
    pytest.fail(f"Native execution did not reach {states}; {state}")


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_changed_project_is_rejected_before_any_native_worker_starts(tmp_path):
    pytest.importorskip("vtkmodules")
    operations, entry = prepare(tmp_path, configuration())
    project = operations.call("project_inspect", {"config_file": "case/config.json"})
    entry.write_text(entry.read_text().replace('"end_time": 0.002', '"end_time": 0.003'))
    with pytest.raises(ValueError, match="changed since inspection"):
        operations.call("submit", {"config_file": "case/config.json", "project": project})
    assert not list(operations.jobs.directory.glob("*/job.json"))


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_repeated_submission_returns_the_same_frozen_job_and_rejects_changed_input(tmp_path):
    operations, _ = prepare(tmp_path, configuration())
    project = operations.call("project_inspect", {"config_file": "case/config.json"})
    arguments = {"config_file": "case/config.json", "project": project, "kind": "geometry", "submission_id": str(uuid.uuid4())}
    first = operations.call("submit", arguments)
    reference = first["reference"]
    try:
        assert operations.call("submit", arguments)["reference"] == reference
        with pytest.raises(ValueError, match="different frozen request"):
            operations.call("submit", {**arguments, "kind": "run"})
        assert len(list(operations.jobs.directory.glob("*/request.json"))) == 1
    finally:
        operations.call("job_cancel", {"reference": reference})


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_real_supervisor_interruption_is_reported_without_automatic_replay(tmp_path, monkeypatch):
    pytest.importorskip("cadquery")
    import subprocess
    import sphinxsim.runtime.jobs as jobs_module
    original = subprocess.Popen
    processes = []
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(jobs_module.subprocess, "Popen", spawn)
    operations = Operations(tmp_path)
    job = operations.call("geometry", {"recipe": {"version": 1, "operations": [
        {"id": "body", "op": "box", "args": {"size": [1, 1, 1]}}], "output": "body"}})
    reference = job["reference"]
    process = processes[0]
    try:
        wait(operations, reference, {"preparing"})
        process.terminate()
        process.wait(timeout=10)
        assert operations.call("job_status", {"reference": reference})["state"] == "interrupted"
        assert operations.call("job_status", {"reference": reference})["state"] == "interrupted"
        assert len(processes) == 1
        assert len(list(operations.jobs.directory.glob("*/request.json"))) == 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_native_particles_continue_once_and_keep_the_original_frozen_input(tmp_path):
    require_native()
    operations, entry = prepare(tmp_path, configuration())
    project = operations.call("project_inspect", {"config_file": "case/config.json"})
    job = operations.call("submit", {"config_file": "case/config.json", "project": project, "kind": "particles", "lease_seconds": 30})
    reference = job["reference"]
    try:
        wait(operations, reference, {"waiting"})
        entry.write_text("{}")
        first = operations.call("job_continue", {"reference": reference})
        assert first["continuation_requested"]
        operations.call("job_continue", {"reference": reference})
        wait(operations, reference, {"succeeded"})
        result = operations.call("results_inspect", {"reference": reference})
        assert result["available"] and not result["partial"]
        assert result["quality"]["checked_frames"] > 0
        assert len(list(operations.jobs.directory.glob("*/job.json"))) == 1
        frozen = operations.jobs.job_path(reference["job_id"]) / "project/config.json"
        assert json.loads(frozen.read_text())["solver_parameters"]["end_time"] == .002
    finally:
        operations.call("job_cancel", {"reference": reference})


@pytest.mark.skipif(os.name != "posix", reason="Detached local jobs explicitly require POSIX")
def test_native_particle_lease_expiry_rejects_continue(tmp_path):
    require_native()
    operations, _ = prepare(tmp_path, configuration())
    project = operations.call("project_inspect", {"config_file": "case/config.json"})
    job = operations.call("submit", {"config_file": "case/config.json", "project": project, "kind": "particles", "lease_seconds": 1})
    reference = job["reference"]
    try:
        wait(operations, reference, {"preview_expired"})
        with pytest.raises(ValueError, match="not waiting|expired"):
            operations.call("job_continue", {"reference": reference})
        assert operations.call("results_inspect", {"reference": reference})["available"]
    finally:
        operations.call("job_cancel", {"reference": reference})


def test_j2_damage_reaches_the_real_native_material_builder(tmp_path):
    # Geometry alone does not construct a material. buildSimulation must run.
    pytest.importorskip("vtkmodules")
    core = require_native()
    config = j2_configuration()
    config["geometries"]["global_resolution"]["particle_spacing"] = .025
    config["geometries"]["shapes"][1]["polygons"][0]["thickness"] = .05
    config["geometries"]["system_domain"] = {"lower_bound": [-.05, -.05], "upper_bound": [.55, .2]}
    config["particle_generation"]["settings"]["relaxation_parameters"]["total_iterations"] = 1
    config["solver_parameters"].update(end_time=.0001, output_interval=.0001)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    simulation = core.SPHSimulation(str(path))
    output = tmp_path / "output"
    output.mkdir()
    simulation.resetOutputRoot(str(output), True)
    simulation.buildGeometries()
    simulation.generateParticles()
    simulation.buildSimulation()
    simulation.initializeSimulation()
    simulation.run()
    from sphinxsim.runtime.export_vtp import Exporter
    exporter = Exporter(output, tmp_path / "verified", config, 2)
    exporter.scan()
    frames = exporter.bodies[config["continuum_bodies"][0]["name"]]
    assert frames[-1]["time"] > 0
    assert frames[-1]["count"] > 0
