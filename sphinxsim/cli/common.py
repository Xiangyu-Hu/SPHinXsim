"""Shared CLI helpers and constants for SPHinXsim."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Tuple

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm.common import dump_simulation_config_json


def _find_project_root(start: str | None = None) -> str:
    """Find the repository root while supporting the package-based CLI layout."""
    start = start or str(Path(__file__).resolve().parent.parent)
    current = start
    while current != os.path.dirname(current):
        candidate = os.path.join(current, "pyproject.toml")
        cli_package = os.path.join(current, "sphinxsim", "cli", "main.py")
        cli_module = os.path.join(current, "sphinxsim", "cli.py")
        if os.path.exists(candidate) and (os.path.isfile(cli_package) or os.path.isfile(cli_module)):
            return current
        current = os.path.dirname(current)
    return os.getcwd()


PROJECT_ROOT = Path(_find_project_root())
__version__ = "0.1.0"


def _load_config(path: Path) -> Tuple[SimulationConfig | None, int]:
    """Load and validate a SimulationConfig from *path*.

    Returns ``(config, 0)`` on success or ``(None, 1)`` after printing an
    error message to stderr.
    """
    if not path.is_absolute():
        cwd_path = Path.cwd() / path
        build_temp_path = PROJECT_ROOT / ".build-temp" / path
        path = cwd_path if cwd_path.exists() else build_temp_path

    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return None, 1
    try:
        data = json.loads(path.read_text())
        from sphinxsim.runtime.physics import check_config

        normalized, _ = check_config(data)
        return SimulationConfig(**normalized), 0
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return None, 1
    except (ValidationError, ValueError) as exc:
        print(f"Config validation failed:\n{exc}", file=sys.stderr)
        return None, 1


def _apply_payload_diff(raw: Any, base: Any, edited: Any) -> Any:
    """Return *raw* with only the differences between *base* and *edited* applied."""
    if base == edited:
        return raw
    if isinstance(raw, dict) and isinstance(base, dict) and isinstance(edited, dict):
        result = dict(raw)
        for key, value in edited.items():
            if key not in base:
                result[key] = value
            elif key not in raw:
                if value != base[key]:
                    result[key] = value
            else:
                result[key] = _apply_payload_diff(raw[key], base[key], value)
        for key in base:
            if key not in edited:
                result.pop(key, None)
        return result
    if (
        isinstance(raw, list)
        and isinstance(base, list)
        and isinstance(edited, list)
        and len(raw) == len(base) == len(edited)
    ):
        return [_apply_payload_diff(r, b, e) for r, b, e in zip(raw, base, edited)]
    return edited


def _write_validated_config(path: Path, config: SimulationConfig, content: str | None = None) -> str:
    """Atomically replace *path* with *content* (default: canonical validated JSON)."""
    if content is None:
        content = dump_simulation_config_json(config, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            suffix=".json",
            prefix=f".{path.stem}.",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return content


def _short_repr(value: Any, max_len: int = 80) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _collect_change_lines(before: Any, after: Any, path: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        lines: list[str] = []
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            key_path = f"{path}.{key}" if path else key
            if key not in before:
                lines.append(f"{key_path}: added {_short_repr(after[key])}")
                continue
            if key not in after:
                lines.append(f"{key_path}: removed")
                continue
            lines.extend(_collect_change_lines(before[key], after[key], key_path))
        return lines

    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        return [f"{path or '<root>'}: list changed (size {len(before)} -> {len(after)})"]

    if before != after:
        return [f"{path or '<root>'}: {_short_repr(before)} -> {_short_repr(after)}"]
    return []

__all__ = [
    "PROJECT_ROOT",
    "__version__",
    "_apply_payload_diff",
    "_collect_change_lines",
    "_find_project_root",
    "_load_config",
    "_short_repr",
    "_write_validated_config",
]
