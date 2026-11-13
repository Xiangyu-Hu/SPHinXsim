---
name: cli
description: Command-layer guidance for the SPHinXsim CLI: parser structure, command dispatch, shell/interactive flow, preview behavior, and safe refactors without touching runtime or schema internals.
---

# SPHinXsim CLI

## Purpose

Use this skill when modifying command-layer behavior for SPHinXsim, especially around the CLI packaging and command execution flow. Keep the scope to the command interface rather than the solver, config schemas, or simulation runtime.

## Invocation triggers

Invoke this skill when:
- editing or refactoring the CLI package under `sphinxsim/cli/`
- splitting a large CLI file into smaller modules
- updating parser wiring, dispatch logic, or shell commands
- debugging command-specific regressions in CLI behavior
- preserving compatibility across `sphinxsim.cli` imports while moving modules into a package layout

## Scope

### Covers
- `sphinxsim/cli/` package structure and public entrypoints
- parser construction and argument validation
- command dispatch and shell parsing
- preview-related CLI commands
- compatibility shims for legacy imports
- CLI-specific tests in `tests/test_cli.py`

### Does not cover
- `sphinxsim/config/` schema and patch logic
- `sphinxsim/runtime/` execution internals
- `sphinxsim/visualization/` rendering behavior
- C++ solver or binding layer changes

## Architecture

The CLI layer is organized by responsibility, and some larger features are split into dedicated modules.

Current structure:
- `main.py` — top-level entrypoint, `main()`, parser setup, command flow
- `preview_runtime.py` — persistent preview runtime and live-particle lifecycle
- `json_editor.py` — Qt-backed JSON property editor for preview config editing
- `common.py` — shared config I/O and diff helpers used by CLI commands
- `generate.py`, `update.py`, `validate.py`, `run.py`, `explore.py`, `shell.py`, `preview.py` — command-specific implementations

The `sphinxsim/cli/__init__.py` package root keeps a compatibility export surface for legacy imports such as
`from sphinxsim.cli import _new_json_editor_list_item` or `from sphinxsim.cli import main` while implementation is allowed to move into dedicated modules.

## Editor extraction guidance

When moving editor logic out of `preview_runtime.py`:
- keep the runtime-facing API stable (`_json_editor` dictionary shape and `refresh` callback behavior)
- move widgets, state management, and save/validate logic into a dedicated module such as `sphinxsim/cli/json_editor.py`
- update the preview runtime to delegate to the extracted editor builder
- preserve old import names if tests or callers still reference them from `sphinxsim.cli`

This especially matters for the JSON property editor because it is UI-heavy and highly coupled to preview logic; isolating it makes the code easier to test without altering the shell preview lifecycle.

## Key conventions

### 1. Public CLI API stability
- Preserve a package entry at `sphinxsim.cli` during refactors.
- Export `main` and the command helpers from the package root so external callers do not break.
- Prefer compatibility shims over a disruptive hard rename.

### 2. Split by responsibility
- Put parser assembly in a dedicated module.
- Keep file I/O and config validation helpers separate from command execution.
- Keep preview/runtime behavior isolated from generic argument parsing.
- Avoid mixing shell parsing, diff logic, and structured execution in one file.

### 3. Minimal blast radius
- Do not move simulation runtime or config schema code into the CLI package.
- Keep CLI refactors within the command layer and its direct dependencies.
- Only touch non-CLI modules when a CLI compatibility issue requires it.

### 4. Backward compatibility
- Existing tests may import names directly from `sphinxsim.cli`.
- Keep module-level API compatibility during the migration even if the implementation is moved under the package directory.

## Validation

After a CLI refactor, verify with the project’s configured environment, typically the repo venv:

```bash
. .venv/bin/activate
pytest -q
python3 -m sphinxsim --help
python3 -m sphinxsim validate --help
```

These checks confirm the package import path, the console entrypoint, and the CLI parser still behave correctly. For this project, the repo-local `.venv` is the authoritative runtime for validation.

## Refactor checklist

When splitting the CLI:
1. Create or update the focused module under `sphinxsim/cli/`.
2. Move the implementation into the right responsibility boundary without changing runtime internals.
3. Add or preserve package-level re-exports in `sphinxsim/cli/__init__.py` for compatibility.
4. Keep the runtime-facing preview/editor contract stable if external code relies on `_json_editor`.
5. Keep `main()` and command dispatch working as the public entrypoint.
6. Run the repo test suite in `.venv` before claiming the refactor is safe.
