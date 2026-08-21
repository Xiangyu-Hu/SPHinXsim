# SPHinXsim Documentation

## Introduction

SPHinXsim is a Python and LLM-facing workflow for building, visualizing, validating, updating, and running SPH simulations on top of the SPHinXsys C++ library.

The project is designed around explicit simulation configuration rather than opaque prompt-only execution. A user can describe a scenario in natural language, generate a structured JSON config, revise that config with further instructions, validate it against strict schemas, and execute it through the SPHinXsys backend.

## What this repository provides

- A Python package, `sphinxsim`, with a CLI for config generation, update, visualization,  validation, and execution.
- Pydantic-based schemas for geometry, materials, particle generation, solver settings, observers, constraints, and recording options.
- LLM adapters with a local mock backend by default and API service based generation when configured.
- Native C++ bindings and simulation builders that bridge validated JSON configs to SPHinXsys runtime components.
- Tests and documentation covering schema rules, CLI behavior, and integrated simulation workflows.

## Core workflow

The recommended user workflow is to use the **interactive shell**:

```bash
sphinxsim shell
```

Inside the shell, you can:

1. **Load** an existing config file: `load config.json`
2. **Generate** a simulation config from natural language: `generate "water dam break" config.json`
3. **Validate** the config structure: `validate`
4. **Update** it with further instructions: `update "simulate for 2 s"`
5. **Explore** the simulator schema and capabilities: `explore what body types are supported?`
6. **Run** the validated simulation: `run`
7. **Update geometry or other fields**: `update "water flow with 5 mm resolution"`
8. **Rerun preview or run** to rebuild from the updated config

In shell mode, you can also use slash-prefixed commands without quotes around the description, for example `/generate water dam break config.json` or `/update simulate for 2 s`.
In shell mode, `validate` reloads the loaded file from disk so external edits are picked up immediately.
Geometry edits stay config-driven in shell mode: update the JSON, then rerun `preview` or `run` to rebuild from the current file.

Alternatively, you can use direct commands for non-interactive workflows:

- `sphinxsim generate` — Create a config from description
- `sphinxsim update` — Modify an existing config
- `sphinxsim explore` — Ask schema and functionality questions
- `sphinxsim validate` — Check config validity
- `sphinxsim run` — Execute a validated simulation
- `sphinxsim preview` — Render an interactive geometry/BC preview before running
- `sphinxsim --generate-completion {bash|zsh|fish}` — Print shell completion scripts

For detailed examples and all command options, see [CLI Usage](cli-usage.md).

## High-level architecture

- `sphinxsim/cli.py`:
  Command-line entry point for `generate`, `update`, `validate`, and `run`. It resolves project-local paths, validates configs before execution, and routes runtime output into project-managed build directories.
- `sphinxsim/config/schemas.py`:
  Defines the top-level `SimulationConfig` and the typed config surface for system domains, global resolution, shapes, oriented boxes, particle generation, fluid bodies, continuum bodies, solid bodies, boundary conditions, observers, restart settings, body constraints, and extra state recording.
- `sphinxsim/llm/`:
  Provides LLM backends that translate natural-language prompts into schema-compliant configs and answer schema exploration questions. The default mock backend supports deterministic local testing, while OpenAI-compatible, NVIDIA, and Ollama backends use the shared `SPHINXSIM_LLM_PROVIDER`, `SPHINXSIM_LLM_MODEL`, `SPHINXSIM_LLM_BASE_URL`, and `SPHINXSIM_LLM_API_KEY` settings.
- `sphinxsim/sph_simulation/` and native bindings:
  Build and load SPHinXsys-backed simulation objects from validated JSON, including fluid and continuum-oriented workflows exposed through the Python package.

## Current capabilities

SPHinXsim currently supports a broader config surface than a basic fluid-only demo workflow:

- Fluid dynamics configurations with typed fluid materials, inflow boundary conditions, observers, and solver controls.
- Continuum dynamics configurations with continuum material models (including `plastic_continuum` for soil/granular cases) and dedicated continuum solver parameters.
- Solid boundary/body definitions required by the current validation and simulation builders.
- Config-driven geometry composition using domains, shapes, oriented boxes, transforms, and particle-generation settings.
- Incremental config editing through the CLI update command instead of regenerating cases from scratch.
- Schema exploration through the CLI explore command for questions about supported bodies, materials, and simulator behavior.

The repository remains config-first: natural-language generation is useful for initialization and revision, but the validated JSON artifact is the authoritative simulation input.

## Current direction

The codebase is positioned for richer multi-physics growth while staying strict about validation boundaries. In practice, that means expanding benchmark coverage, continuing to improve continuum and coupled workflows, and strengthening the path from prompt to executable, testable simulation assets.

## LLM test matrix

The repository now supports two LLM testing modes:

- Mocked tests are always safe for CI and run without any external service.
- Ollama integration tests run locally when Ollama is available and are skipped in CI.

See [LLM testing](llm-testing.md) for the exact test matrix, environment variables, and local commands.

## Why this project matters

SPHinXsys provides strong performance and physical modeling, but direct setup can be expensive for rapid iteration. SPHinXsim narrows that gap by combining:

- A structured Python interface for orchestration and validation.
- LLM-assisted config authoring and revision.
- Native execution against SPHinXsys kernels once the config is validated.

Together, these pieces provide a practical path from idea to executable simulation with clearer structure, stronger reproducibility, and lower onboarding friction.
