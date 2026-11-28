"""Discover and instantiate verified 2D and 3D SPHinXsim reference templates."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from sphinxsim.config.migrations import migrate_legacy_configuration
from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.json_editor.json_format import format_json_like_source
from sphinxsim.runtime.export_vtp import hash_file
from sphinxsim.runtime.physics import check_config


def _default_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").is_file() and (current / "sphinxsim" / "cli.py").is_file():
            return current
        current = current.parent
    return Path.cwd().resolve()


def _collect_asset_names(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "file_name" and isinstance(child, str) and child.strip():
                found.append(child)
            else:
                found.extend(_collect_asset_names(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_asset_names(child))
    return sorted(set(found))


def _candidate_sources(root: Path) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    sim_2d = root / "tests" / "test_simulation" / "test_2d_simulation" / "data"
    if sim_2d.is_dir():
        for path in sorted(sim_2d.glob("*.json")):
            candidates.append((f"2d/{path.stem}", path))

    sim_3d = root / "tests" / "test_simulation" / "test_3d_simulation" / "data"
    if sim_3d.is_dir():
        for path in sorted(sim_3d.glob("*.json")):
            candidates.append((f"3d/{path.stem}", path))

    examples_dir = root / "examples" / "input"
    if examples_dir.is_dir():
        for path in sorted(examples_dir.glob("*/*.json")):
            candidates.append((f"examples/{path.parent.name}", path))

    return candidates


def list_templates(project_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Return metadata for all verified reference templates in *project_root*."""
    root = Path(project_root).resolve() if project_root is not None else _default_project_root()
    templates: list[dict[str, Any]] = []
    for template_id, path in _candidate_sources(root):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            payload = migrate_legacy_configuration(raw)
            normalized, report = check_config(payload)
            config = SimulationConfig.model_validate(normalized)
        except Exception:
            continue

        bodies = (
            [f"{b.name} (fluid:{b.material.type.value})" for b in config.fluid_bodies]
            + [f"{b.name} (continuum:{b.material.type.value})" for b in config.continuum_bodies]
            + [f"{b.name} (solid:{b.material.type.value})" for b in config.solid_bodies]
        )
        templates.append(
            {
                "id": template_id,
                "name": path.stem,
                "dimension": report["dimension"],
                "simulation_type": config.simulation_type.value,
                "bodies": bodies,
                "assets": _collect_asset_names(payload),
                "source_path": str(path),
            }
        )
    return templates


def resolve_template(
    template_id: str,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve a template by full ID (``2d/dambreak``) or unambiguous stem (``column_collapse``)."""
    cleaned = template_id.strip().strip("/").lower()
    if not cleaned:
        raise ValueError("Template identifier must not be empty")

    catalog = list_templates(project_root)
    if not catalog:
        raise ValueError("No reference templates are available in this installation")

    exact = [item for item in catalog if item["id"].lower() == cleaned]
    if len(exact) == 1:
        return exact[0]

    by_stem = [item for item in catalog if item["name"].lower() == cleaned]
    if len(by_stem) == 1:
        return by_stem[0]
    if len(by_stem) > 1:
        options = ", ".join(item["id"] for item in by_stem)
        raise ValueError(f"Ambiguous template {template_id!r}; choose one of: {options}")

    available = ", ".join(item["id"] for item in catalog)
    raise ValueError(f"Unknown template {template_id!r}. Available templates: {available}")


def _atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content.encode("utf-8"))
        temporary.replace(destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def init_template(
    template_id: str,
    destination: str | Path,
    *,
    project_root: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a verified template and its referenced assets to *destination*."""
    entry = resolve_template(template_id, project_root)
    source = Path(entry["source_path"]).resolve()
    target = Path(destination).resolve()

    if target.suffix.lower() != ".json":
        raise ValueError("Destination configuration path must end with .json")
    if source == target:
        raise ValueError("Destination path must differ from the template source")
    if target.exists() and not overwrite:
        raise ValueError(f"Destination already exists: {target} (use --overwrite to replace)")

    original_text = source.read_bytes().decode("utf-8")
    payload = migrate_legacy_configuration(json.loads(original_text))
    staged_assets: list[str] = []

    def stage_assets(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key != "file_name" or child is None:
                    stage_assets(child)
                    continue
                resource = Path(str(child))
                if not resource.is_absolute():
                    resource = (source.parent / resource).resolve()
                if not resource.is_file():
                    raise ValueError(f"Template asset not found: {resource}")
                if resource.suffix.lower() not in (".stl", ".txt", ".dat"):
                    raise ValueError(f"Unsupported template asset suffix: {resource.suffix}")
                digest = hash_file(resource)
                rel_asset = Path("assets") / resource.name
                dest_asset = target.parent / rel_asset
                dest_asset.parent.mkdir(parents=True, exist_ok=True)
                temp_asset: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=dest_asset.parent, delete=False
                    ) as handle:
                        temp_asset = Path(handle.name)
                    shutil.copyfile(resource, temp_asset)
                    if hash_file(temp_asset) != digest:
                        raise ValueError("Template asset changed during copy")
                    temp_asset.replace(dest_asset)
                    temp_asset = None
                finally:
                    if temp_asset is not None:
                        temp_asset.unlink(missing_ok=True)
                rel_posix = rel_asset.as_posix()
                value[key] = rel_posix
                if rel_posix not in staged_assets:
                    staged_assets.append(rel_posix)
        elif isinstance(value, list):
            for child in value:
                stage_assets(child)

    stage_assets(payload)
    normalized, report = check_config(payload)
    SimulationConfig.model_validate(normalized)
    formatted = format_json_like_source(payload, original_text, source)
    _atomic_write_text(target, formatted)

    return {
        "template": entry["id"],
        "config_file": str(target),
        "dimension": report["dimension"],
        "simulation_type": entry["simulation_type"],
        "bodies": entry["bodies"],
        "assets": staged_assets,
        "sha256": hash_file(target),
    }
