"""Update command logic for the SPHinXsim CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch, apply_update_patch
from sphinxsim.llm import get_llm

from .common import _collect_change_lines, _load_config


def _print_update_summary(before_cfg: SimulationConfig, after_cfg: SimulationConfig) -> None:
    before = before_cfg.model_dump(exclude_none=True)
    after = after_cfg.model_dump(exclude_none=True)
    changes = _collect_change_lines(before, after)
    if not changes:
        print("ℹ️ No effective changes were applied to the config.")
        return

    print("Changes applied:")
    for line in changes[:12]:
        print(f"  - {line}")
    if len(changes) > 12:
        print(f"  - ... and {len(changes) - 12} more changes")


def cmd_update(args: argparse.Namespace) -> int:
    """Update an existing SimulationConfig from a natural-language instruction."""
    config_path = Path(args.config_file)
    config, rc = _load_config(config_path)
    if rc != 0:
        return rc
    assert config is not None

    llm = get_llm()
    try:
        if not hasattr(llm, "update"):
            print(
                "The selected LLM provider does not support config updates. "
                "Please use a provider implementing update().",
                file=sys.stderr,
            )
            return 1
        strict_mode = str(getattr(args, "strict", "true")).lower() != "false"
        if getattr(args, "patch_mode", False):
            if not hasattr(llm, "update_patch"):
                print(
                    "The selected LLM provider does not support patch-mode updates. "
                    "Please use a provider implementing update_patch().",
                    file=sys.stderr,
                )
                return 1
            patch_payload = llm.update_patch(config, args.description, strict=strict_mode)
            if isinstance(patch_payload, UpdatePatch):
                parsed_patch = patch_payload
            else:
                parsed_patch = UpdatePatch.model_validate(patch_payload)

            patch_result = apply_update_patch(
                config.model_dump(exclude_none=True), parsed_patch, strict=strict_mode
            )
            if patch_result.errors:
                print("Error applying update patch:", file=sys.stderr)
                for error in patch_result.errors:
                    print(f"  - {error}", file=sys.stderr)
                return 1

            try:
                updated_config = SimulationConfig.model_validate(patch_result.updated)
            except ValidationError as exc:
                print(f"Patched config validation failed:\n{exc}", file=sys.stderr)
                return 1

            print("Patch summary:")
            print(f"  Applied: {patch_result.applied}")
            print(f"  Changed: {patch_result.changed}")
            print(f"  Operations: {patch_result.summary}")
            print(f"  Diff stats: {patch_result.diff_stats}")
            if patch_result.warnings:
                print("  Warnings:")
                for warning in patch_result.warnings:
                    print(f"    - {warning}")

            if getattr(args, "dry_run", False):
                print("Dry run: no files were written.")
                print("Generated patch:")
                print(parsed_patch.model_dump_json(indent=2, exclude_none=True))
                return 0
        else:
            updated_config = llm.update(config, args.description)
    except (ValueError, ValidationError) as exc:
        print(f"Error updating config: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error updating config: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else config_path

    output = updated_config.model_dump_json(indent=2, exclude_none=True)
    try:
        if output_path.parent and not output_path.parent.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
    except OSError as exc:
        print(f"Error writing updated config to {output_path}: {exc}", file=sys.stderr)
        return 1

    if args.output:
        print(f"Updated config written to {output_path}")
    else:
        print(f"Updated config in place: {output_path}")

    _print_update_summary(config, updated_config)
    return 0


__all__ = ["_print_update_summary", "cmd_update"]
