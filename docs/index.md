# SPHinXsim Documentation

## Introduction

SPHinXsim is a Python and LLM interface for building and running multi-physics SPH simulations on top of the SPHinXsys C++ library.

The project focuses on making simulation setup easier without losing scientific structure. Instead of writing low-level C++ for every case, users can describe a scenario in natural language, generate a structured JSON configuration, validate it against strict schemas, and run it through the SPHinXsys backend.

## What this repository provides

- A Python package (`sphinxsim`) with CLI and LLM integration.
- Pydantic-based simulation schemas for robust config validation.
- C++ binding integration with SPHinXsys simulation kernels.
- Documentation and tests for development and user workflows.

## Core workflow

The intended user loop is:

1. Describe a simulation in natural language.
2. Generate JSON config (`sphinxsim generate`).
3. Validate schema and physical consistency (`sphinxsim validate`).
4. Run simulation (`sphinxsim run`).
5. Iterate on parameters and rerun.

This structure keeps configuration explicit and reproducible while still allowing faster setup with LLM assistance.

## High-level architecture

- `sphinxsim/cli.py`:
 Command-line entry point for generate/validate/run.
- `sphinxsim/config/schemas.py`:
 Pydantic models for domain bounds, fluid bodies, solid bodies, observers, solver options, and consistency checks.
- `sphinxsim/llm/`:
 LLM backends (including OpenAI and mock mode) that transform natural-language prompts into schema-compliant configs.
- `sphinxsim/sph_simulation/` and bindings:
 Bridge to compiled SPHinXsys C++ components used at runtime.

## Current scope and direction

SPHinXsim is currently a strong foundation for:

- Fluid-focused simulation setup.
- Config-first simulation reproducibility.
- Python-driven experimentation around SPHinXsys.

It is designed to grow toward richer multi-physics coverage (for example, more complete solid and FSI pipelines), stronger benchmark suites, and broader end-to-end automation from prompt to validated simulation artifacts.

## Why this project matters

SPHinXsys is powerful but can be complex to access for rapid iteration. SPHinXsim narrows that gap by combining:

- Performance and physical modeling from SPHinXsys.
- Usability and orchestration in Python.
- Fast scenario prototyping through LLM-assisted config generation.

Together, this enables a practical path from idea to executable simulation with clearer structure, stronger validation, and lower onboarding friction.
