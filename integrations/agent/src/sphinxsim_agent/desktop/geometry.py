"""Per-request native geometry build; never imports Qt or starts a solver."""
import ctypes
import json
import os
from pathlib import Path
import sys

if os.environ.get("TCM_ROOT"):
    ctypes.CDLL("libhwloc.so.15", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)
    ctypes.CDLL("libtcm.so.1", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.bindings.loader import load_sphinxsys_core_nd
entry, directory = map(Path, sys.argv[1:])
config = json.loads((directory / "config.json").read_text())
root = (Path(os.environ.get("SPHINXSIM_WORK_ROOT", "/work")) / "project").resolve()

def resources(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "file_name":
                target = (entry.parent / child).resolve(strict=True)
                if not target.is_relative_to(root) or target.suffix.lower() not in (".stl", ".txt"):
                    raise ValueError("Resource outside project")
                value[key] = os.path.relpath(target, directory)
            elif key == "name" and isinstance(child, str):
                if not child or len(child) > 128 or any(c in child for c in "/\\\x00\r\n") or ".." in child:
                    raise ValueError("Unsafe native object name")
            else:
                resources(child)
    elif isinstance(value, list):
        for child in value:
            resources(child)
resources(config)
from sphinxsim_agent.physics import check_config
normalized, _ = check_config(config)
validated = SimulationConfig.model_validate(normalized)
path = directory / "effective.json"
path.write_text(validated.model_dump_json(exclude_none=True))
output = directory / "output"
output.mkdir()
core = load_sphinxsys_core_nd(validated._infer_spatial_dim())
builder = core.GeometryBuilder(str(path))
builder.resetInOutputRoot(str(output))
builder.buildGeometries()
