---
name: json-editor
description: "Guidance for the SPHinXsim preview JSON configuration editor and JSON formatting. Use when changing or debugging sphinxsim/json_editor, its CLI or desktop integration, editor state, or JSON round-tripping."
---

# SPHinXsim JSON Editor

Use this skill when changing the preview configuration editor, its JSON formatting
and round-tripping behavior, or code that integrates with the editor.

## Scope

- `sphinxsim/json_editor/editor.py` — Qt editor UI, state, editing actions,
  validation, saving, refresh, and editor hooks.
- `sphinxsim/json_editor/json_format.py` — JSON layout preservation and raw
  floating-point formatting.
- `sphinxsim/json_editor/__init__.py` — package exports.
- `sphinxsim/cli.py` — `_ShellPreviewRuntime` integration.
- `sphinxsim/desktop/` — desktop session and presentation integration.
- `tests/test_cli.py`, `tests/runtime/test_desktop.py`, and relevant
  visualization tests.

## Architecture and behavior

- The preview runtime inherits `JsonEditorMixin`; keep editor UI and editor-only
  helpers in the `sphinxsim.json_editor` package rather than growing `cli.py`.
- The package exposes `JsonEditorMixin` and JSON formatting utilities through
  `sphinxsim.json_editor`. Prefer these package imports for new consumers.
- The desktop layer also accesses the editor hooks and tree. Check both CLI and
  desktop call sites before changing the `_json_editor` hook shape or refresh
  behavior.
- Qt is optional and is imported lazily when installing the editor. Preserve
  graceful operation when Qt is unavailable.
- Preserve sparse source JSON when saving: only apply actual edits, avoid adding
  schema defaults or omitted `null` values, and retain existing float text and
  object layout when possible.
- Keep validation before writing, preserve the current error/status feedback,
  and use the runtime's configured writer for saved content.
- Preserve editor state behavior, including undo/revert, dirty-state handling,
  reload/discard confirmation, list editing, and preview refresh cancellation.

## Workflow

1. Read the relevant editor and formatter code, then trace its CLI and desktop
   consumers before changing interfaces.
2. Reuse existing helpers and package exports. Keep Qt-specific code inside the
   editor implementation and avoid introducing a Qt dependency into formatting
   utilities.
3. Add or update focused tests for the behavior change. Cover both serialization
   and editor state when the change affects saving or refresh.
4. Run the focused tests with the project virtual environment, for example:

   ```sh
   ./.venv/bin/pytest tests/test_cli.py tests/runtime/test_desktop.py -q
   ```

5. Run `./.venv/bin/python -m compileall -q sphinxsim/json_editor sphinxsim/cli.py`
   and `git diff --check` for code changes.
