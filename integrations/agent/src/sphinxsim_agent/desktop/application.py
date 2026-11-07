"""Bounded SPHinXsim application operations on the managed Qt thread.

These operations update the same native editor and renderer as human input.
They never create a second CLI, solver, or arbitrary command interpreter.
"""
import base64
from copy import deepcopy
import hashlib
import json
from datetime import datetime, timezone


def patch_config(payload, changes):
    if not isinstance(changes, list) or not 0 < len(changes) <= 64:
        raise ValueError("Provide 1–64 configuration changes")
    updated = deepcopy(payload)
    for change in changes:
        path = change.get("path")
        if not isinstance(path, list) or not 0 < len(path) <= 32:
            raise ValueError("Use an existing configuration field path")
        target = updated
        for part in path[:-1]:
            if isinstance(target, list) and type(part) is int and 0 <= part < len(target):
                target = target[part]
            elif isinstance(target, dict) and isinstance(part, str) and part in target:
                target = target[part]
            else:
                raise ValueError("Configuration path not found")
        key = path[-1]
        if not ((isinstance(target, dict) and isinstance(key, str) and key in target) or
                (isinstance(target, list) and type(key) is int and 0 <= key < len(target))):
            raise ValueError("Configuration field not found")
        if isinstance(target[key], (dict, list)) or isinstance(change.get("value"), (dict, list)):
            raise ValueError("Update scalar fields; use the native editor for structural changes")
        target[key] = change["value"]
    return updated


def execute(command, *, runtime, entry, config_type, save, schedule, intent, current_job, state, label, modeling=None):
    name = command.get("command")
    args = command.get("arguments") or {}
    editor = runtime._json_editor
    from sphinxsim_agent.physics import catalog, check_config, compile_config
    if name == "capabilities":
        result = dict(catalog())
        # Full native schema is opt-in and should not occupy every Tool response.
        if not args.get("includeSchema", False):
            result.pop("schema")
        from sphinxsim_agent.modeling import recipe_contract
        result["modeling"] = recipe_contract()
        return result
    if name == "compile":
        config, report = compile_config(args.get("specification"))
        return {"config": config, "report": report, "applied": False}
    if name in ("geometry_generate", "geometry_status", "geometry_cancel"):
        if modeling is None:
            raise ValueError("Geometry generation is unavailable in this desktop build")
        return modeling.command(name, args, dirty=editor["is_dirty"](),
            frozen=bool(state.get("readOnly") or state.get("intent") or not editor["tree"].isEnabled()),
            automation=command.get("automationId"))
    if name == "screenshot":
        from qtpy import QtCore
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        # Capture the native application, including visible Qt annotations/panels.
        window = runtime.plotter.app_window
        origin = window.mapToGlobal(QtCore.QPoint(0, 0))
        # QWidget.grab misses the native VTK child surface. Capture visible X pixels.
        pixmap = window.screen().grabWindow(0, origin.x(), origin.y(), window.width(), window.height())
        if not pixmap.save(buffer, "PNG"):
            raise ValueError("Native screenshot failed")
        return {"capturedAt": datetime.now(timezone.utc).isoformat(), "result": {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(bytes(buffer.data())).decode()}]}}
    raw = entry.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    normalized, report = check_config(json.loads(raw))
    config = config_type.model_validate(normalized)
    if name == "validate":
        return {"valid": True, "savedConfigHash": digest, "dirty": editor["is_dirty"](),
                "config": config.model_dump(mode="json", exclude_none=True), "report": report,
                "note": "Schema validation of saved configuration; not physical accuracy certification."}
    if name == "save":
        if state.get("readOnly") or not editor["tree"].isEnabled():
            raise ValueError("Configuration is read-only")
        editor["shortcut"].activated.emit()
        if editor["is_dirty"]():
            raise ValueError(editor["status"].text())
        if modeling and modeling.job and modeling.job["state"] == "applied" and modeling.job.get("persistence") == "failed" and not state.get("intent"):
            intent("save")
            modeling.job["persistence"] = "pending"
            modeling.persist()
        return {"savedConfigHash": hashlib.sha256(entry.read_bytes()).hexdigest(), "saved": True}
    if name in ("particles", "continue", "cancel") and (editor["is_dirty"]() or state.get("intent") or state.get("readOnly")):
        raise ValueError("Save changes or wait for the pending operation; result views are read-only")
    if name == "cancel":
        intent("cancel")
    elif name in ("particles", "continue"):
        if name == "continue" and current_job["state"] not in ("waiting", "preview_expired"):
            raise ValueError("Generate particles and wait for readiness before continuing")
        intent(name)
    elif name in ("update", "apply_configuration", "preview"):
        if editor["is_dirty"]():
            raise ValueError("Unsaved native edits are preserved; save or resolve them before applying a command")
        if state.get("intent") or not editor["tree"].isEnabled():
            raise ValueError("Configuration is frozen by the current execution")
        if name in ("update", "apply_configuration"):
            if args.get("expectedHash") != digest:
                raise ValueError("Configuration changed; validate again before updating")
            if name == "update":
                payload = patch_config(config.model_dump(mode="json", exclude_none=True), args.get("changes"))
                normalized, report = check_config(payload)
                if report["normalizations"]:
                    raise ValueError("Configuration values would change during validation; compile and review before applying")
            else:
                normalized, report = compile_config(args.get("specification"))
                if args.get("reviewedHash") != report["configurationHash"]:
                    raise ValueError("Compile and review this configuration before applying its configurationHash")
            config = config_type.model_validate(normalized)
            save(entry, config)
            editor["refresh"](config, entry, False)
        schedule(config, resolved_config_path=entry, with_particles=False, force=True)
        label.setText("Application command applied; rebuilding native geometry…")
        return {"savedConfigHash": hashlib.sha256(entry.read_bytes()).hexdigest(), "building": True}
    else:
        raise ValueError("Unsupported SPHinXsim application command")
    return {"intent": state.get("intent"), "runId": current_job.get("runId")}
