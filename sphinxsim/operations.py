"""Application operations shared by the native CLI and optional MCP adapter.

The owner chooses a workspace and, optionally, a native desktop reference.
Inputs identify configurations, frozen projects and jobs explicitly.
"""
import json
import os
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator


CONTRACT = json.loads(files("sphinxsim.runtime").joinpath("schemas/contracts-v1.json").read_text())
OBJECT = {"type": "object"}
PATH = {"type": "string", "minLength": 1, "maxLength": 240}
REFERENCE = {"type": "object", "properties": {
    "job_id": {"type": "string", "format": "uuid"},
    "request_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
    "required": ["job_id", "request_sha256"], "additionalProperties": False}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TOOLS = {
    "capabilities": ("Describe native schema, routing evidence and CAD limits; not physical certification.", schema({"include_schema": {"type": "boolean"}})),
    "validate": ("Validate the explicitly referenced native configuration without writing it.", schema({"config_file": PATH}, ["config_file"])),
    "compile": ("Return a new native configuration and explicit normalization diagnostics; never apply implicitly.", schema({"specification": OBJECT}, ["specification"])),
    "project_inspect": ("Return the explicit file inventory and hashes to review before submitting a project.", schema({"config_file": PATH}, ["config_file"])),
    "geometry": ("Generate verified STL/STEP. Detached jobs are the default; synchronous generation requires an explicit new output directory.", schema({"recipe": CONTRACT["$defs"]["cad_recipe"], "detach": {"type": "boolean"}, "output": PATH}, ["recipe"])),
    "submit": ("Submit a reviewed project inventory. Changed inputs are rejected before starting native execution.", schema({
        "config_file": PATH, "project": OBJECT, "submission_id": {"type": "string", "format": "uuid"}, "kind": {"enum": ["geometry", "particles", "run"]},
        "lease_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "refresh_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        "wall_seconds": {"type": "integer", "minimum": 1, "maximum": 259200}}, ["config_file", "project"])),
    "job_status": ("Read an explicitly identified persistent job without replaying submission.", schema({"reference": REFERENCE}, ["reference"])),
    "job_logs": ("Read at most the last 16 KiB of native logs.", schema({"reference": REFERENCE}, ["reference"])),
    "job_continue": ("Continue the same frozen particle instance within its lease, idempotently.", schema({"reference": REFERENCE}, ["reference"])),
    "job_cancel": ("Explicitly cancel this calculation; disconnecting does not cancel it.", schema({"reference": REFERENCE}, ["reference"])),
    "results_inspect": ("Verify published hashes and report saved-output quality limits.", schema({"reference": REFERENCE}, ["reference"])),
    "desktop": ("Operate the explicitly enabled native application using an observed instance and version. No remote input driver is included.", schema({
        "operation": {"enum": ["status", "apply", "save", "preview", "particles", "continue", "cancel", "result", "edit", "screenshot", "stop"]},
        "instance_id": {"type": "string"}, "version": {"type": "integer", "minimum": 0},
        "configuration": OBJECT, "submission_id": {"type": "string", "format": "uuid"}, "expected_hash": {"type": "string"}, "reference": REFERENCE,
        "automation_id": {"type": "string", "minLength": 1, "maxLength": 128}}, ["operation"])),
}


class Operations:
    def __init__(self, root, *, desktop_reference=None):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.desktop_reference = desktop_reference

    @property
    def jobs(self):
        from .runtime.jobs import JobStore
        return JobStore(self.root)

    def call(self, name, arguments):
        if name not in TOOLS:
            raise ValueError("Unknown operation")
        if len(json.dumps(arguments, allow_nan=False).encode()) > 1 << 20:
            raise ValueError("Operation input exceeds 1 MiB")
        Draft202012Validator(TOOLS[name][1]).validate(arguments)
        if name == "capabilities":
            from .runtime.physics import catalog
            from .runtime.modeling import recipe_contract
            result = dict(catalog())
            if not arguments.get("include_schema"):
                result.pop("schema", None)
            return {**result, "modeling": recipe_contract(), "execution": {
                "detached_local_jobs": os.name == "posix", "max_active_jobs": 4,
                "resource_limits": "The process owner supplies CPU/memory/GPU isolation"},
                "desktop": {"enabled": self.desktop_reference is not None, "computer_use": "external provider"}}
        if name == "compile":
            from .runtime.physics import compile_config
            configuration, report = compile_config(arguments["specification"])
            return {"configuration": configuration, "report": report, "applied": False}
        if name == "validate":
            from .runtime.physics import check_config
            path = self.jobs.path(arguments["config_file"])
            if path.stat().st_size > 1 << 20:
                raise ValueError("Entry configuration exceeds 1 MiB")
            configuration, report = check_config(json.loads(path.read_text()))
            return {"configuration": configuration, "report": report}
        if name == "project_inspect":
            return self.jobs.inspect_project(arguments["config_file"])
        if name == "geometry":
            if arguments.get("detach", True):
                return self.jobs.generate(arguments["recipe"])
            if not arguments.get("output"):
                raise ValueError("Synchronous geometry generation needs an explicit output directory")
            from .runtime.modeling import generate
            return generate(arguments["recipe"], self.jobs.path(arguments["output"], exists=False))
        if name == "submit":
            return self.jobs.submit(arguments["config_file"], expected_project=arguments["project"],
                submission_id=arguments.get("submission_id"), kind=arguments.get("kind", "run"), lease_seconds=arguments.get("lease_seconds", 600),
                refresh_seconds=arguments.get("refresh_seconds", 10), wall_seconds=arguments.get("wall_seconds", 3600))
        if name == "desktop":
            if self.desktop_reference is None:
                raise ValueError("Desktop access was not enabled by the process owner")
            from .desktop.client import request
            return request(self.desktop_reference, arguments)
        methods = {"job_status": self.jobs.status, "job_logs": self.jobs.logs,
                   "job_continue": self.jobs.continue_job, "job_cancel": self.jobs.cancel,
                   "results_inspect": self.jobs.results}
        return methods[name](self.jobs.resolve(arguments["reference"]))
