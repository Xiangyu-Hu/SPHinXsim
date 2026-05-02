# SPHinXsim Documentation

## Introduction

SPHinXsim is a Python and LLM-facing workflow for building, validating, updating, and running SPH simulations on top of the SPHinXsys C++ library.

The project is designed around explicit simulation configuration rather than opaque prompt-only execution. A user can describe a scenario in natural language, generate a structured JSON config, revise that config with further instructions, validate it against strict schemas, and execute it through the SPHinXsys backend.

## What this repository provides

- A Python package, `sphinxsim`, with a CLI for config generation, update, validation, and execution.
- Pydantic-based schemas for geometry, materials, particle generation, solver settings, observers, constraints, and recording options.
- LLM adapters with a local mock backend by default and OpenAI-backed generation when configured.
- Native C++ bindings and simulation builders that bridge validated JSON configs to SPHinXsys runtime components.
- Tests and documentation covering schema rules, CLI behavior, and integrated simulation workflows.

## Core workflow

The current user loop is:

1. Describe a simulation in natural language.
2. Generate a JSON config with `sphinxsim generate`.
3. Refine an existing config with `sphinxsim update`.
4. Validate structure and cross-field consistency with `sphinxsim validate`.
5. Run the validated case with `sphinxsim run`.
6. Iterate on geometry, materials, solver settings, and rerun.

This keeps simulation inputs reproducible and reviewable while still allowing fast iteration through LLM assistance.

## High-level architecture

- `sphinxsim/cli.py`:
  Command-line entry point for `generate`, `update`, `validate`, and `run`. It resolves project-local paths, validates configs before execution, and routes runtime output into project-managed build directories.
- `sphinxsim/config/schemas.py`:
  Defines the top-level `SimulationConfig` and the typed config surface for system domains, global resolution, shapes, aligned boxes, particle generation, fluid bodies, continuum bodies, solid bodies, boundary conditions, observers, restart settings, body constraints, and extra state recording.
- `sphinxsim/llm/`:
  Provides LLM backends that translate natural-language prompts into schema-compliant configs. The default mock backend supports deterministic local testing, while the OpenAI backend can be enabled with environment variables such as `SPHINXSIM_LLM_PROVIDER`, `OPENAI_API_KEY`, and `OPENAI_MODEL`.
- `sphinxsim/sph_simulation/` and native bindings:
  Build and load SPHinXsys-backed simulation objects from validated JSON, including fluid and continuum-oriented workflows exposed through the Python package.

## Current capabilities

SPHinXsim currently supports a broader config surface than a basic fluid-only demo workflow:

- Fluid dynamics configurations with typed fluid materials, inflow boundary conditions, observers, and solver controls.
- Continuum dynamics configurations with continuum material models and dedicated continuum solver parameters.
- Solid boundary/body definitions required by the current validation and simulation builders.
- Config-driven geometry composition using domains, shapes, aligned boxes, transforms, and particle-generation settings.
- Incremental config editing through the CLI update command instead of regenerating cases from scratch.

The repository remains config-first: natural-language generation is useful for initialization and revision, but the validated JSON artifact is the authoritative simulation input.

## Current direction

The codebase is positioned for richer multi-physics growth while staying strict about validation boundaries. In practice, that means expanding benchmark coverage, continuing to improve continuum and coupled workflows, and strengthening the path from prompt to executable, testable simulation assets.

## Why this project matters

SPHinXsys provides strong performance and physical modeling, but direct setup can be expensive for rapid iteration. SPHinXsim narrows that gap by combining:

- A structured Python interface for orchestration and validation.
- LLM-assisted config authoring and revision.
- Native execution against SPHinXsys kernels once the config is validated.

Together, these pieces provide a practical path from idea to executable simulation with clearer structure, stronger reproducibility, and lower onboarding friction.
