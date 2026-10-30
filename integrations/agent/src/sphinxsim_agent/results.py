"""Verify published resource references before presenting results to an agent."""
import json
from pathlib import Path
import re

from .export_vtp import hash_file


def inspect_results(directory, manifest):
    directory = Path(directory).resolve(strict=True)

    def asset(name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", name) or ".." in name:
            raise ValueError("Invalid result asset reference")
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or unsafe result asset")
        return path

    content = json.loads(asset(manifest).read_text())
    checked = set()

    def verify(value):
        if isinstance(value, dict):
            if "source" in value:
                name = value["source"]
                if not isinstance(name, str) or not re.fullmatch(r"source-[a-f0-9]{64}\.vtp", name):
                    raise ValueError("Invalid source VTP reference")
                if name not in checked and hash_file(asset(name)) != name[7:-4]:
                    raise ValueError("Source VTP hash mismatch")
                checked.add(name)
            if {"file", "bytes", "sha256"}.issubset(value):
                path = asset(value["file"])
                if path.stat().st_size != value["bytes"] or hash_file(path) != value["sha256"]:
                    raise ValueError("Result asset size/hash mismatch")
                checked.add(value["file"])
            for child in value.values():
                verify(child)
        elif isinstance(value, list):
            for child in value:
                verify(child)
    verify(content)
    report = content.get("quality", {}).get("report")
    quality = json.loads(asset(report["file"]).read_text()) if report else None
    bodies = content.get("bodyMetadata", {})
    # Reports remain complete on disk; unbounded field/body findings must not
    # become an agent's tool context. Delivery uses verified resources instead.
    for key, value in (("quality", quality), ("bodies", bodies)):
        if len(json.dumps(value, ensure_ascii=True).encode()) > 16384:
            if key == "quality":
                quality = {"state": value.get("state"), "checkedFrames": value.get("checkedFrames"),
                    "findingCount": len(value.get("findings", [])), "bodyCount": len(value.get("bodies", {})),
                    "truncated": True, "scope": "Decoded saved frames only; not physical certification. Read the full report for checks not evaluated.", "resource": asset(report["file"]).as_uri()}
            else:
                bodies = {"count": len(value), "truncated": True}
    return {"manifest": manifest, "phase": content.get("phase"), "partial": content.get("partial", False),
        "verifiedAssets": len(checked), "bodies": bodies,
        "quality": quality,
        "resource": asset(manifest).as_uri()}
