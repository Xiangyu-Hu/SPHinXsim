"""Generate command logic for the SPHinXsim CLI."""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sphinxsim.config.schemas import PhysicalCorrectionWarning, SimulationConfig
from sphinxsim.llm import get_llm as _default_get_llm
from sphinxsim.llm.common import LLMRepairWarning, dump_simulation_config_json


def _resolve_get_llm():
    """Return the CLI-level factory when tests or callers monkeypatch the package root."""
    try:
        import sphinxsim.cli as cli_pkg
    except Exception:
        return _default_get_llm
    candidate = getattr(cli_pkg, "get_llm", None)
    return candidate if callable(candidate) else _default_get_llm


def _generate_with_physical_correction_feedback(llm: Any, description: str) -> Any:
    """Generate a config and report deterministic corrections and LLM repairs."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", PhysicalCorrectionWarning)
        warnings.simplefilter("always", LLMRepairWarning)
        config = llm.generate(description)

    for warning in caught:
        if issubclass(warning.category, PhysicalCorrectionWarning):
            print(f"Physical correction applied: {warning.message}", file=sys.stderr)
        elif issubclass(warning.category, LLMRepairWarning):
            print(f"LLM repair applied: {warning.message}", file=sys.stderr)
        else:
            warnings.showwarning(
                warning.message,
                warning.category,
                warning.filename,
                warning.lineno,
            )
    return config


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate a SimulationConfig from a natural-language *description*."""
    llm = _resolve_get_llm()()
    try:
        config = _generate_with_physical_correction_feedback(llm, args.description)
    except (ValueError, ValidationError) as exc:
        print(f"Error generating config: {exc}", file=sys.stderr)
        return 1

    output = dump_simulation_config_json(config, indent=2)
    if args.output:
        output_path = Path(args.output)
        try:
            if output_path.parent and not output_path.parent.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output)
        except OSError as exc:
            print(f"Error writing config to {output_path}: {exc}", file=sys.stderr)
            return 1
        print(f"Config written to {output_path}")
    else:
        print(output)
    return 0


__all__ = ["_generate_with_physical_correction_feedback", "cmd_generate"]
