"""Fixed headless host-neutral entrypoint. A retained solver accepts only one continue command."""
import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
import time

from sphinxsim_agent.export_vtp import Exporter, atomic_json
from sphinxsim_agent.project_input import load_project


def event(phase, **values):
    print(json.dumps({"phase": phase, **values}), flush=True)


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    options = request
    kind = options.get("kind", "run") or "run"
    if kind not in ("run", "geometry", "particles"):
        raise ValueError("Unsupported execution kind")
    if not options.get("project"):
        raise ValueError("Only frozen custom projects are supported")
    from importlib.resources import files
    from jsonschema import Draft202012Validator
    contract = json.loads(files("sphinxsim_agent").joinpath("schemas/contracts-v1.json").read_text())
    Draft202012Validator({**contract, "$ref": "#/$defs/execution"}).validate(options)
    config, dimension = load_project(options["project"], Path.cwd() / "project", options.get("solverDefaults"))
    from sphinxsim.config.schemas import SimulationConfig
    from sphinxsim.bindings.loader import load_sphinxsys_core_nd
    validated = SimulationConfig.model_validate(config)
    if validated._infer_spatial_dim() != dimension:
        raise ValueError("Project dimension mismatch")
    work = Path.cwd()
    config_path = work / "effective-config.json"
    config_path.write_text(validated.model_dump_json(exclude_none=True, indent=2))
    output = work / "solver"
    if output.exists():
        raise ValueError("Refusing to reuse an existing solver output")
    output.mkdir()
    # Intel TCM loads hwloc dynamically. Under the non-root container, its
    # exit destructor can call an already unloaded topology function (SIGSEGV).
    # Pin both runtime mappings before the solver loads them, through normal C++ cleanup.
    if os.environ.get("TCM_ROOT"):
        ctypes.CDLL("libhwloc.so.15", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)
        ctypes.CDLL("libtcm.so.1", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)
    sph = load_sphinxsys_core_nd(dimension)
    from sphinxsim_agent.physics import check_config
    _, admission = check_config(config)
    atomic_json(work / "configuration-report.json", admission)
    exporter = Exporter(output, work / "result", config, dimension, options.get("configRevision", 0),
        {"snapshotId": options["project"].get("id"), "snapshotSHA256": options["project"].get("sha256"),
         "attempt": options.get("attempt"), "buildFingerprint": admission["buildFingerprint"],
         "adapterRevision": admission["adapterRevision"], "imageId": admission["imageId"]})
    started = time.time()
    event("preparing")
    # A separate geometry builder prevents preview output from changing the retained solver's IO root.
    if kind == "geometry":
        builder = sph.GeometryBuilder(str(config_path))
        builder.resetInOutputRoot(str(output))
        builder.buildGeometries()
        exporter.export_geometry(builder.getShapeBounds())
        name, _ = exporter.publish("geometry")
        atomic_json(work / "published.json", {"state": "preview_ready", "snapshot": name})
        event("preview_ready")
        return
    sim = sph.SPHSimulation(str(config_path))
    sim.resetOutputRoot(str(output), True)
    sim.buildGeometries()
    exporter.export_geometry(sim.getShapeBounds())
    name, _ = exporter.publish("geometry")
    atomic_json(work / "published.json", {"state": "preparing", "snapshot": name})
    event("generating_particles")
    sim.generateParticles()
    # Build from the final reload particles, so the preview remains accurate
    # even when relaxation iterations do not end on the recorder's 100-step interval.
    for path in output.rglob("*.vtp"):
        if not path.name.startswith("Shape"):
            path.unlink()
    sim.buildSimulation()
    sim.initializeSimulation()
    exporter.scan(preview=True)
    exporter.particle_bodies = dict(exporter.bodies)
    if kind == "particles":
        if not exporter.bodies.get(exporter.primary):
            raise ValueError("No generated primary particles")
        name, _ = exporter.publish("particles")
        lease_seconds = options.get("previewLeaseSeconds", 600) or 600
        expires = time.time() + lease_seconds
        atomic_json(work / "published.json", {"state": "waiting", "snapshot": name, "leaseExpiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat()})
        event("waiting")
        while True:
            control = work / "control.json"
            if control.exists():
                command = json.loads(control.read_text())
                if command == {"command": "continue", "attempt": options.get("attempt")} and time.time() < expires:
                    break
            if time.time() >= expires:
                atomic_json(work / "published.json", {"state": "preview_expired", "snapshot": name})
                event("preview_expired")
                return
            time.sleep(.2)
    exporter.bodies, exporter.fields, exporter.seen = {}, {}, {}
    event("running")
    interval = options.get("refreshIntervalSeconds", 10) or 10
    done = threading.Event()

    def collect():
        while not done.wait(interval):
            try:
                exporter.scan()
                name, _ = exporter.publish("results")
                atomic_json(work / "published.json", {"state": "running", "snapshot": name})
            except (ValueError, OSError) as exc:
                # Incomplete/changing files are retried; last verified snapshot remains visible.
                event("output_retry", reason=str(exc))

    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    try:
        sim.run() # Binding releases GIL; collection never touches the solver instance.
    finally:
        done.set()
        collector.join()
    completion = {"completed": True, "requestedEndTime": config.get("solver_parameters", {}).get("end_time"), "durationSeconds": time.time() - started, "dimension": dimension}
    atomic_json(work / "solver-completion.json", completion)
    event("postprocessing")
    exporter.scan()
    if not exporter.bodies.get(exporter.primary):
        raise ValueError("No primary simulation frames")
    name, _ = exporter.publish("results", completed=True)
    atomic_json(work / "published.json", {"state": "succeeded", "snapshot": name})
    event("succeeded")

if __name__ == "__main__":
    main()
