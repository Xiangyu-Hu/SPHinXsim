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

The CLI layer should be organized around responsibility, not by keeping everything in a single file.

Recommended split:
- `main.py` — top-level entrypoint, `main()`, parser setup, command flow
- `parser.py` — argument registration and parser factory functions
- `structured.py` — structured command registration/execution
- `preview.py` — preview runtime and window behaviors
- `interactive.py` — shell input, completion, REPL-style command handling
- `config_io.py` — file loading and validation helpers used by CLI commands

Keep the public import surface stable through package-level re-exports so imports such as
`from sphinxsim.cli import main` continue to work during the migration.

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

After a CLI refactor, verify with:

```bash
python3 -m pytest tests/test_cli.py -q
python3 -m sphinxsim --help
python3 -m sphinxsim validate --help
```

These checks confirm the package import path, the console entrypoint, and the CLI parser still behave correctly.

## Refactor checklist

When splitting the CLI:
1. Create the package directory under `sphinxsim/cli/`.
2. Move the implementation into focused modules.
3. Add package-level re-exports in `sphinxsim/cli/__init__.py`.
4. Keep a stable `main()` entrypoint.
5. Update any direct script references if needed.
6. Run the CLI-focused tests.
