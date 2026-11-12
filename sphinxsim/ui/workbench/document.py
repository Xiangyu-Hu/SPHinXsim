"""Thread-safe draft and reviewed-proposal state, independent of Qt and Chainlit."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import tempfile
import threading

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.runtime.physics import check_config


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rebase_asset_paths(value, source_dir):
    """Rebase schema file_name entries when a case snapshot moves directories."""
    if isinstance(value, dict):
        result = {key: rebase_asset_paths(child, source_dir) for key, child in value.items()}
        if isinstance(result.get("file_name"), str):
            result["file_name"] = str((Path(source_dir) / result["file_name"]).resolve())
        return result
    if isinstance(value, list):
        return [rebase_asset_paths(child, source_dir) for child in value]
    return value


class WorkbenchDocument:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.actions = Queue()
        self.proposal = None
        self.display = "Configuration preview"
        self.manual_run_active = False
        self.load(path)

    def load(self, path):
        path = Path(path).resolve()
        text = path.read_bytes().decode("utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Configuration must be a JSON object")
        for shape in payload.get("geometries", {}).get("shapes", []):
            if shape.get("type") != "multipolygon":
                continue
            for polygon in shape.get("polygons") or []:
                if polygon.get("type") != "container_box":
                    continue
                legacy_keys = ("inner_lower_bound", "inner_upper_bound")
                if not any(key in polygon for key in legacy_keys):
                    continue
                if polygon.get("inner_box") is not None:
                    raise ValueError("container_box contains both inner_box and legacy inner bounds")
                if not all(key in polygon for key in legacy_keys):
                    raise ValueError("Legacy container_box requires inner_lower_bound and inner_upper_bound")
                polygon["inner_box"] = {
                    "lower_bound": polygon.pop("inner_lower_bound"),
                    "upper_bound": polygon.pop("inner_upper_bound"),
                }
        check_config(payload)
        from sphinxsim.json_format import format_json_like_source
        draft = format_json_like_source(payload, text, path)
        with self.lock:
            self.path = path
            self.text = draft
            self.saved_text = text
            self.proposal = None

    def set_text(self, text):
        with self.lock:
            self.text = text

    def snapshot(self):
        with self.lock:
            return dict(config_path=str(self.path), text=self.text,
                        revision=fingerprint(str(self.path) + self.text),
                        dirty=self.text != self.saved_text, display=self.display,
                        manual_run_active=self.manual_run_active)

    def validate(self):
        with self.lock:
            configuration, _ = check_config(json.loads(self.text))
            return SimulationConfig.model_validate(configuration)

    def save(self):
        with self.lock:
            self.validate()
            if self.path.read_bytes().decode("utf-8") != self.saved_text:
                raise ValueError("The file changed on disk. Reopen it before saving.")
            # Preserve LF/CRLF exactly; text-mode writes on Windows otherwise
            # translate newlines a second time for existing CRLF documents.
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent, delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(self.text.encode("utf-8"))
                os.replace(temporary_path, self.path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            self.saved_text = self.text

    def propose(self, base_revision, payload, instruction):
        check_config(payload)
        with self.lock:
            if self.snapshot()["revision"] != base_revision:
                raise ValueError("The draft changed during the LLM request. Ask again using the current draft.")
            from sphinxsim.json_format import format_json_like_source
            text = format_json_like_source(payload, self.text, self.path)
            diff = "".join(difflib.unified_diff(self.text.splitlines(True), text.splitlines(True),
                                            fromfile="Current draft", tofile="LLM proposal"))
            self.proposal = dict(base_revision=base_revision, text=text,
                                 instruction=instruction, diff=diff)
            return self.proposal.copy()

    def apply_proposal(self):
        with self.lock:
            if not self.proposal:
                raise ValueError("There is no pending proposal.")
            if self.snapshot()["revision"] != self.proposal["base_revision"]:
                raise ValueError("The draft changed. Discard this proposal and request a new one.")
            self.text = self.proposal["text"]
            self.proposal = None
            return self.text

    def pending_proposal(self):
        with self.lock:
            return self.proposal.copy() if self.proposal else None
