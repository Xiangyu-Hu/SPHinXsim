# CLI Usage Guide

SPHinXsim provides a command-line interface for building, validating, updating, and running SPH simulations. This guide covers all available commands and workflows.

## Quick start

### Interactive shell mode (recommended)

The easiest way to get started is the interactive shell:

```bash
sphinxsim shell
```

This opens an interactive prompt where you can enter commands sequentially:

```
> generate "water dam break simulation" config.json
✓ Config generated and written to .../.build-temp/config.json
✓ Schema validation passed

> validate
Configuration: WaterBody (fluid) + WallBoundary (solid)
  Domain: [0, 0] to [5.37, 5.37]
  Resolution: 0.025 m
  End time: 0.5 s
  Gravity: [0, -1] m/s²

> update "simulate for 2 s"
✓ Updated config written to .../.build-temp/config.json
✓ Schema validation passed

> explore what bodies and materials does this simulation support?
SPHinXsim supports fluid, continuum, and solid body definitions, with schema-validated material types.

> validate
Configuration: WaterBody (fluid) + WallBoundary (solid)
  Domain: [0, 0] to [5.37, 5.37]
  Resolution: 0.025 m
  End time: 2.0 s
  Gravity: [0, -1] m/s²

> run
✅ Simulation configuration loaded
✅ Simulation initialized
🚀 Running simulation...

> lock-status
Geometry lock status: locked (source: simulator)

> update "water flow with 5 mm resolution"
Geometry is locked after particle generation. Unlock geometry first to apply geometry changes.

> unlock-geometry
🔓 Geometry updates unlocked (simulator-reported state).

> update "water flow with 5 mm resolution"
✓ Updated config written to .../.build-temp/config.json
✓ Schema validation passed

> exit
Goodbye!
```

### Shell commands

Inside the shell, you can use the following commands:

| Command | Description |
| --- | --- |
| `load FILE` | Load and validate an existing config file |
| `generate "description" FILE` | Generate a new config and write it to FILE |
| `update "instruction"` | Modify the loaded config with an instruction (e.g., "change end time to 5 s") |
| `update --patch-mode "instruction"` | Apply operation-based patch updates |
| `update --patch-mode --dry-run "instruction"` | Preview patch update without writing |
| `update --patch-mode --strict false "instruction"` | Use non-strict patch apply behavior |
| `explore "question"` | Ask the configured LLM questions about the simulator schema and capabilities |
| `validate` | Reload the loaded file from disk and validate it |
| `preview` | Render an interactive geometry/BC preview of the loaded config |
| `preview --no-cpp` | Preview using schema bounding-box fallback only (no C++ build) |
| `preview --screenshot FILE` | Save a screenshot to FILE instead of opening an interactive window |
| `run` | Build and execute the loaded config |
| `lock-geometry` | Lock geometry updates for the active shell session |
| `unlock-geometry` | Unlock geometry updates (and reset downstream simulator state when attached) |
| `lock-status` | Show whether geometry updates are locked |
| `help` | Show available commands |
| `exit` | Quit the shell |

Notes:
- `sphinxsim shell` starts with no file loaded.
- Relative file paths inside the shell resolve from the current directory first, then fall back to `.build-temp/`.
- `validate` always reloads from disk, so external edits are picked up immediately.

## Geometry lock behavior

The simulator now acts as the source of truth for geometry lock state during shell workflows.

- Geometry becomes locked after particle generation in the simulator lifecycle.
- While locked, geometry-changing `update` operations are rejected.
- Non-geometry updates (for example, end time changes) are still allowed.
- `unlock-geometry` re-opens geometry edits. If a simulator instance is attached, this calls the simulator reset path so downstream particle/system/solver state is invalidated safely.
- `lock-status` reports whether the lock state comes from the simulator or shell fallback state.

In non-interactive direct commands (`sphinxsim update ...`), there is no persistent simulator session, so lock enforcement is session policy rather than live simulator state.

## Direct commands (non-interactive)

You can also run individual commands directly:

### Generate

Create a new simulation config from a natural language description:

```bash
sphinxsim generate "2D water dam break with 0.5 m/s initial velocity" --output config.json
```

This:
1. Sends your description to the LLM provider (mock by default, or Ollama if configured)
2. Receives a JSON config in response
3. Validates the config against strict schemas
4. Saves the result to `config.json`
5. Prints a summary

### Validate

Check an existing config without modifying it:

```bash
sphinxsim validate config.json
```

This displays:
- Simulation type (fluid_dynamics, continuum_dynamics, or coupled)
- List of bodies and their material types
- Domain bounds and resolution
- Solver parameters (end time, output interval, etc.)
- Any validation errors

### Update

Modify an existing config with natural language instructions:

```bash
sphinxsim update config.json "increase end time to 10 s" --output config_updated.json
```

This:
1. Loads the existing config
2. Sends it to the LLM provider along with your instruction
3. Receives a modified JSON config
4. Validates the updated config
5. Saves the result to `config_updated.json`

### Explore

Ask the configured LLM questions about the simulator schema, supported bodies, materials, and workflow behavior:

```bash
sphinxsim explore "What body types are valid in SimulationConfig?"
```

This:
1. Sends your question and schema context to the selected LLM provider
2. Returns a plain-text explanation of the simulator schema and capabilities
3. Uses the same provider selection as `generate` and `update`

### Run

Execute a validated simulation:

```bash
sphinxsim run config.json
```

This:
1. Validates the config
2. Builds SPHinXsys simulation components in C++
3. Runs the simulation
4. Saves output to `build-integrated/output`

### Preview

Render an interactive 3-D geometry/BC preview before running the solver:

```bash
sphinxsim preview config.json
```

This:
1. Validates the config
2. Attempts to invoke `buildGeometries()` from the C++ extension to produce accurate VTP meshes
3. Opens an interactive PyVista window with colour-coded bodies, oriented boxes, and annotations

Options:

| Flag | Description |
| --- | --- |
| `--no-cpp` | Skip C++ geometry build; render only the system domain bounding box and annotations |
| `--off-screen` | Render off-screen (no window) — useful for automated testing |
| `--screenshot FILE` / `-s FILE` | Save a screenshot to FILE instead of opening a window. Implies `--off-screen`. |

Requires the optional `[visualization]` extra:

```bash
pip install sphinxsim[visualization]
```

See [Visualization](visualization.md) for full details.

## Workflow examples

### Example 1: Quick iteration with the shell

```bash
sphinxsim shell
> generate "2D water dam break, domain 5m x 5m, resolution 2.5cm" config.json
> validate
> run
> exit
```

### Example 2: Compare two configurations

```bash
sphinxsim generate "water dam break" --output config_v1.json
sphinxsim validate config_v1.json

sphinxsim generate "water dam break with faster gravity" --output config_v2.json
sphinxsim validate config_v2.json

# Then run the version you prefer:
sphinxsim run config_v1.json
```

### Example 3: External editing loop in shell

```bash
sphinxsim shell
> load config.json
> validate
# edit config.json in your editor while shell remains open
> validate
> update "change particle spacing to 1 cm"
> explore what materials can I use for solid bodies?
> validate
> run
```

### Example 4: Batch process with direct commands

```bash
for desc in "water dam break" "sloshing tank" "wave propagation"; do
  sphinxsim generate "$desc" --output "config_$desc.json"
  sphinxsim validate "config_$desc.json"
done
```

### Example 5: Preview before running

```bash
sphinxsim shell
> generate "2D heat transfer in a channel" config.json
> validate
> preview                   # inspect geometry and BCs interactively
> preview --no-cpp          # quick bounding-box fallback if C++ not built
> preview --screenshot preview.png   # save a screenshot for a report
> run
> exit
```

### Example 6: Geometry edit safety loop in shell

```bash
sphinxsim shell
> load config.json
> run
> lock-status
> update "water flow with 5 mm resolution"     # rejected while locked
> unlock-geometry
> update "water flow with 5 mm resolution"     # now allowed
> validate
> run
```

### Example 7: Soil column-collapse workflow (continuum dynamics)

```bash
# Validate and run the soil benchmark config
sphinxsim validate tests/test_simulation/test_2d_simulation/data/column_collapse.json
sphinxsim run tests/test_simulation/test_2d_simulation/data/column_collapse.json

# Optional: iterate in shell
sphinxsim shell
> load tests/test_simulation/test_2d_simulation/data/column_collapse.json
> explore what material fields are required for plastic_continuum?
> validate
> run
```

This case uses continuum dynamics with a `plastic_continuum` material model for granular/soil behavior.

## LLM provider selection

By default, `sphinxsim` uses a local mock LLM that works offline. To use a different provider:

### Use Ollama (local LLM inference)

```bash
export SPHINXSIM_LLM_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=qwen2.5:3b
sphinxsim generate "water dam break"
```

First, ensure Ollama is running:

```bash
ollama serve
# In another terminal:
ollama pull qwen2.5:3b
```

### Use OpenAI

```bash
export SPHINXSIM_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4
sphinxsim generate "water dam break"
```

### Use NVIDIA NIM (OpenAI-compatible API)

```bash
export SPHINXSIM_LLM_PROVIDER=nvidia_nim
export NVIDIA_NIM_API_KEY=nvapi-...
export NVIDIA_NIM_MODEL=z-ai/glm-5.2
export NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
sphinxsim generate "water dam break"
```

`NVIDIA_API_KEY` is also accepted for compatibility.

### Use mock (default)

```bash
export SPHINXSIM_LLM_PROVIDER=mock
sphinxsim generate "water dam break"
```

## Output locations

- **Generated configs**: Printed to stdout unless `--output` is provided
- **Generated configs with `--output`**: Written to the exact path you provide (relative to your current directory)
- **Shell-generated configs**: Saved to the FILE argument used by `generate "..." FILE` (resolved from current directory first, then `.build-temp/`)
- **Explore answers**: Printed to stdout; no files are written
- **Simulation output**: Saved under `.build-temp/test_simulation/` (runtime output root)
- **Temporary files**: Stored in `.build-temp/`

## Error handling

If config generation or validation fails:

1. **Generation fails**: The LLM response did not match the expected JSON schema. Check the error message for details, or try rephrasing your description.
2. **Validation fails**: The config violates a schema constraint (e.g., body type mismatch). Use `sphinxsim validate` to see which field is invalid.
3. **Explore fails**: The LLM could not answer using the schema context. Rephrase the question to focus on supported bodies, materials, solver settings, or CLI workflow.
4. **Execution fails**: The config is valid but the simulation failed. Check simulation output in `.build-temp/test_simulation/`.

## See also

- [Visualization](visualization.md) for the pre-run geometry/BC preview
- [LLM Testing](llm-testing.md) for local testing with mock and Ollama
- [Schema reference](index.md#current-capabilities) for supported simulation types and materials
