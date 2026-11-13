"""Schema exploration command logic for the SPHinXsim CLI."""

from __future__ import annotations

import argparse
import json
import sys

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.llm import get_llm as _default_get_llm


def _resolve_get_llm():
    """Return the package-level factory when tests patch sphinxsim.cli.get_llm."""
    try:
        import sphinxsim.cli as cli_pkg
    except Exception:
        return _default_get_llm
    candidate = getattr(cli_pkg, "get_llm", None)
    return candidate if callable(candidate) else _default_get_llm


def _schema_explore_context() -> str:
    schema = json.dumps(SimulationConfig.model_json_schema(), indent=2)
    return (
        "You are helping a user understand the SPHinXsim simulator schema and capabilities. "
        "Answer clearly and concisely using the schema as the source of truth. "
        "When relevant, include practical command examples.\n\n"
        "CLI capabilities:\n"
        "- generate: create a config from natural language\n"
        "- update: revise an existing config with natural language\n"
        "- validate: schema-validate and summarize a config\n"
        "- run: execute simulation from validated config\n"
        "- shell: interactive mode for generate/update/validate/run\n\n"
        "SimulationConfig JSON schema:\n"
        f"{schema}"
    )


def cmd_explore(args: argparse.Namespace) -> int:
    """Answer schema/functionality questions using the configured LLM provider."""
    question = args.question.strip() if args.question else ""
    if not question:
        print("question must not be empty", file=sys.stderr)
        return 1

    llm = _resolve_get_llm()()
    if not hasattr(llm, "explore"):
        print(
            "The selected LLM provider does not support schema exploration.",
            file=sys.stderr,
        )
        return 1

    try:
        answer = llm.explore(question, context=_schema_explore_context())
    except Exception as exc:
        print(f"Error exploring schema: {exc}", file=sys.stderr)
        return 1

    print("Top-level SimulationConfig fields and guidance:")
    print(answer)
    return 0


__all__ = ["_schema_explore_context", "cmd_explore"]
