"""Deterministic, filesystem-safe case preparation tools for Agent plans."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
from typing import Any


class CaseToolError(ValueError):
    """Raised when a case cannot be copied or patched safely."""


class CaseWorkspace:
    """Create and edit JSON case copies below one Agent task-store root."""

    def __init__(self, root: str | Path, *, source_root: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.source_root = Path(source_root or Path.cwd()).resolve()

    def prepare_case(
        self,
        source_config_path: str | Path,
        output_config_path: str | Path,
    ) -> Path:
        """Copy an existing JSON case without modifying the source file."""
        source = self._source_path(source_config_path)
        destination = self._workspace_path(output_config_path)
        if not source.is_file():
            raise CaseToolError(f"source configuration does not exist: {source}")
        if source.suffix.lower() != ".json" or destination.suffix.lower() != ".json":
            raise CaseToolError("case preparation requires JSON source and output paths")
        if source == destination:
            raise CaseToolError("source and output configuration paths must be different")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CaseToolError(f"unable to prepare case copy {destination}: {exc}") from exc
        return destination

    def update_friction_angle(
        self,
        config_path: str | Path,
        friction_angle_degrees: float,
        *,
        body_name: str,
    ) -> tuple[Path, float]:
        """Set one plastic-continuum body's friction angle, storing radians."""
        path = self._workspace_path(config_path)
        if not path.is_file():
            raise CaseToolError(f"working configuration does not exist: {path}")
        if isinstance(friction_angle_degrees, bool) or not isinstance(
            friction_angle_degrees, (int, float)
        ):
            raise CaseToolError("friction_angle_degrees must be a number")
        angle = float(friction_angle_degrees)
        if not math.isfinite(angle) or not 0.0 <= angle < 90.0:
            raise CaseToolError("friction_angle_degrees must satisfy 0 <= angle < 90")
        if not isinstance(body_name, str) or not body_name.strip():
            raise CaseToolError("body_name must be a non-empty string")

        payload = self._read_json_object(path)
        bodies = payload.get("continuum_bodies")
        if not isinstance(bodies, list):
            raise CaseToolError("configuration must contain a continuum_bodies list")

        matches: list[dict[str, Any]] = []
        for body in bodies:
            if isinstance(body, dict) and body.get("name") == body_name:
                matches.append(body)
        if len(matches) != 1:
            raise CaseToolError(
                f"expected exactly one continuum body named {body_name!r}, found {len(matches)}"
            )

        material = matches[0].get("material")
        if not isinstance(material, dict):
            raise CaseToolError(f"continuum body {body_name!r} has no material object")
        if material.get("type") != "plastic_continuum":
            raise CaseToolError(
                f"continuum body {body_name!r} must use plastic_continuum material"
            )

        angle_radians = math.radians(angle)
        material["friction_angle"] = angle_radians
        self._atomic_write_json(path, payload)
        return path, angle_radians

    def _source_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.source_root / path
        return path.resolve()

    def _workspace_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise CaseToolError(f"case output must remain inside Agent workspace: {self.root}")
        return resolved

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaseToolError(f"unable to read valid JSON configuration {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise CaseToolError("configuration root must be a JSON object")
        return payload

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        from .store import AgentTaskStore, StoreError
        try:
            AgentTaskStore._atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        except (OSError, StoreError) as exc:
            raise CaseToolError(f"unable to write configuration {path}: {exc}") from exc
