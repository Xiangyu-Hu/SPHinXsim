# CLI Usage Guide

SPHinXsim provides a command-line interface for building, validating, updating, and running SPH simulations. This guide covers all available commands and workflows.

Structured JSON, CadQuery modeling, persistent execution and MCP use the same
CLI. See [Native CLI and external agents](agent-workflows.md).

## AI-assisted workflows

`config.json` on disk is the single source of truth for simulation inputs, and every operation is available directly from the `sphinxsim` CLI.

- **Multi-step agentic CLI & shell (`ask`)**: Run `sphinxsim ask "<request>" -c config.json` (or `ask <request>` / `/ask <request>` inside `sphinxsim shell`) to execute multi-step workflows over in-process SPHinXsim operations (`templates`, `template_init`, `config_patch`, `validate`, `geometry`, `submit`/`job_*`, `results_summary`). Validated edits to `config.json` preserve formatting and write a `.bak` backup file.
- **Single-step prompt commands**: Use `generate`, `update`, and `explore` in the CLI or interactive shell for direct prompt-assisted configuration authoring and schema Q&A. Provider selection is shared through `SPHINXSIM_LLM_PROVIDER`; see [LLM testing](llm-testing.md) for setup examples.

For a graphical workflow with an embedded Chat panel, reviewed configuration
proposals, and Preview, start the Workbench:

```bash
python -m sphinxsim workbench
```

For durable AgentCAE calibration, start with `python -m sphinxsim agent --help`.
The default workflow requires explicit approval before it runs a solver. The
agent stores its state in a workspace so you can inspect, approve, resume, or
report a workflow in a later process. See
[AgentCAE calibration](agent-cae.md) for a complete example and the supported
workflow scope.

## Quick start

### Shell auto-completion

Generate completion scripts directly from the CLI:

```bash
sphinxsim --generate-completion bash
sphinxsim --generate-completion zsh
sphinxsim --generate-completion fish
```

You can evaluate the script for the current shell session:

```bash
# bash
eval "$(sphinxsim --generate-completion bash)"

# zsh
eval "$(sphinxsim --generate-completion zsh)"

# fish
sphinxsim --generate-completion fish | source
```

For persistent completion, add the command to your shell startup file
(`~/.bashrc`, `~/.zshrc`, or `~/.config/fish/config.fish`).

Troubleshooting:
- If completion is not active in the current terminal, reload your shell config:

```bash
# bash
source ~/.bashrc

# zsh
source ~/.zshrc

# fish
source ~/.config/fish/config.fish
```

- For zsh, ensure completion is initialized before evaluating the script:

```zsh
autoload -Uz compinit
compinit
eval "$(sphinxsim --generate-completion zsh)"
```

- To verify script output quickly:

```bash
sphinxsim --generate-completion bash | head
```

### Interactive shell mode (recommended)

The easiest way to get started is the interactive shell:

```bash
sphinxsim shell
# or load a configuration immediately on startup:
sphinxsim shell path/to/config.json
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

> update "water flow with 5 mm resolution"
> update "water flow with 5 mm resolution"
✓ Updated config written to .../.build-temp/config.json
✓ Schema validation passed

> exit
Goodbye!
```

On interactive terminals, press **Tab** to complete shell commands and paths
for file arguments such as `load`, `generate`, and `preview --screenshot`.
Path completion works with nested directories and `~` paths; directories are
completed with a trailing slash so you can continue into them. Use the **Up**
and **Down** arrow keys to navigate commands entered during the current shell
session.
On Windows, the installed `pyreadline3` dependency provides this support.
Windows paths may use forward slashes or backslashes; quote paths containing spaces.

In shell mode you can also use slash-prefixed commands without quotes around the description, for example:

```text
sphinxsim> /generate water dam break simulation config.json
sphinxsim> /update simulate for 2 s
```

### Shell commands

Inside the shell, you can use the following commands:

| Command | Description |
| --- | --- |
| `templates [--json]` | List verified 2D and 3D reference templates |
| `init TEMPLATE FILE [--overwrite]` | Initialize a config (and stage referenced `.stl`/`.dat` assets) from a template and load it |
| `restore [FILE] [--dry-run]` | Restore a config atomically from its `.bak` backup (`undo` alias also supported) |
| `load FILE` | Load and validate an existing config file |
| `generate "description" FILE` | Generate a new config and write it to FILE |
| `update "instruction"` | Modify the loaded config with an instruction (e.g., "change end time to 5 s") |
| `update --patch-mode "instruction"` | Apply operation-based patch updates |
| `update --patch-mode --dry-run "instruction"` | Preview patch update without writing |
| `update --patch-mode --strict false "instruction"` | Use non-strict patch apply behavior |
| `ask [--dry-run] "request"` | Run a multi-step agentic turn over in-process SPHinXsim CLI operations (`/ask` and `?` also supported) |
| `explore "question"` | Ask the configured LLM questions about the simulator schema and capabilities |
| `validate` | Reload the loaded file from disk and validate it |
| `preview` | Render an interactive geometry/BC preview of the loaded config |
| `preview --watch [--watch-output-dir DIR]` | Watch `config.json` (and optional output VTPs) on disk and auto-refresh when clean |
| `preview --with-particles` | Generate particles and automatically play their relaxation snapshots |
| `preview --screenshot FILE [--renderer vtk\|cycles] [--fluid-surface] [--save-bundle DIR]` | Save a VTK or Blender Cycles screenshot to FILE, or export a portable Cycles scene bundle |
| `initialize-simulation` | Build and initialize the retained particle-preview simulation and display its initial body-state snapshots |
| `continue-to-run` | Run the initialized particle-preview simulation; check each body's latest snapshot every 75 ms |
| `run [--output-dir DIR]` | Build and execute the loaded config (optionally writing to an isolated `DIR`) |
| `run --detach [--json]` | Submit a persistent background job and save its reference to `.sphinxsim/last-job.json` |
| `run --with-animation` | Preview generated particles, continue that same simulation, and animate its snapshots |
| `job <status\|logs\|wait\|continue\|cancel\|results> [REF_JSON]` | Inspect, wait on, or control a detached job (defaults to `.sphinxsim/last-job.json` when omitted) |
| `results [--output-dir DIR] [--screenshot FILE] [--renderer vtk\|cycles] [--fluid-surface] [--save-bundle DIR] [--color-by FIELD] [--frame FRAME] [--json]` | Summarize VTP frames, `.dat` observer/energy series, quality checks, and optional VTK or Blender Cycles renders (`--frame latest\|first\|all\|N`) |
| `capabilities` / `compile` / `geometry` / `project` | Direct access to native structured SPHinXsim CLI subcommands |
| `agent <start\|status\|approve\|report\|promote\|preview\|...>` | Manage AgentCAE calibration workflows, observe runs in the shell preview, or `promote` candidates back to `config.json` |
| `replay` | Replay simulation snapshots from the latest successful animated run at up to 25 FPS |
| `replay --relaxation` | Replay relaxation snapshots after particle preview at up to 25 FPS |
| `help` | Show available commands |
| `exit` | Quit the shell |

Notes:
- `sphinxsim shell` starts with no file loaded unless a path is passed (`sphinxsim shell case.json`).
- Relative file paths inside the shell resolve from the current directory first, then fall back to `.build-temp/`.
- In shell mode, slash-prefixed commands are also accepted, e.g. `/init 2d/column_collapse case.json`, `/ask set end_time to 0.2 s`, or `/update simulate for 2 s`.
- When `ask` initializes or generates a configuration while no file is loaded, the shell automatically loads that new configuration for subsequent `validate`, `preview`, `run`, and `results` commands.
- `preview --watch` polls `config.json` on disk: external edits (from an IDE, CLI command, `update`, `ask`, or `agent promote`) automatically reload the property editor and rebuild the scene whenever there are no unsaved local GUI edits. If the GUI property tree has unsaved changes, local edits are preserved and a status notice is shown. Starting `run --output-dir DIR` or `run --detach` while `preview --watch` is active automatically streams new VTP snapshots from that run directory.
- `validate` always reloads from disk, so external edits are picked up immediately.
- In shell mode, `preview` keeps a persistent window and returns control to the prompt. Running `preview` again updates the same window.
- `preview --with-particles` plays relaxation snapshots in the Qt-backed preview and retains its generated-particle simulation for `initialize-simulation`, followed by `continue-to-run`. `replay --relaxation` also retains it; other shell commands discard it. Initialization stops relaxation playback and displays the initial simulation state without running the solver.
- `run` without options is unchanged. `run --with-animation` performs the equivalent of `preview --with-particles`, `initialize-simulation`, and `continue-to-run`.
- `continue-to-run` checks each body's latest complete VTP file every 75 ms. When a newer snapshot is ready, the preview refreshes that body's particles immediately instead of queuing earlier snapshots for playback. Polling stops when the simulation terminates. Live refresh requires the Qt-backed persistent preview with `pyvistaqt`; the simulation still runs when that preview is unavailable, but its particles are not updated live.
- Use `replay --relaxation` to replay the `_ite_` snapshots from particle generation, even before initialization. After a successful `run --with-animation` or `continue-to-run`, use `replay` for simulation snapshots only. Replay opens a Qt-backed window if needed, keeps the camera view fixed, and is capped at 25 FPS. Automatic playback is skipped with a warning if Qt playback is unavailable.
- Particle output uses a shared directory. Filename filtering separates relaxation and simulation, but older snapshots of the same kind can still appear in playback.
- For responsive persistent preview, install the `visualization` extra, which
  provides `pyvistaqt`; PySide6 is installed with the base package.
- In the Qt-backed preview, edit settings in the searchable property tree on the right and press `Ctrl+S`; only then are changes validated, saved, and rendered. Click the **Setting ▾** column header for **Expand**, **Collapse**, **Revert**, and **Reload**. To modify list entries, right-click the list row or entry and choose an available action from its context menu. If `preview` would reset unapplied edits, it asks for confirmation first.
- For the complete editor, particle legend and color-bar menu actions, see
  [JSON editor menus](visualization.md#json-editor-menus) and
  [Particle legend context menu](visualization.md#particle-legend-context-menu).
- To switch configurations while the Preview window remains open, run `load OTHER.json` followed by `preview`; the property editor is rebound to the newly loaded file.
- If the persistent Preview window is closed, run `preview` again to create a new window.

## Geometry update workflow

Shell workflows do not keep a persistent geometry lock state.

- Geometry changes can be applied with `update` whenever needed.
- After changing geometry, rerun `validate`, `preview`, or `run` to rebuild from the updated JSON config.
- `run` always rebuilds the simulation from the current config before executing it.

In non-interactive direct commands (`sphinxsim update ...`), the same config-first behavior applies: the updated JSON is the source of truth for the next `preview` or `run`.

## Direct commands (non-interactive)

You can also run individual commands directly:

### Templates and Init

List verified 2D and 3D reference templates and initialize a working configuration (including any referenced `.stl`/`.dat` assets under `assets/`) without requiring an LLM:

```bash
sphinxsim templates
sphinxsim templates --json

sphinxsim init 2d/column_collapse case.json
sphinxsim init 3d/fuel_tank fuel_case/case.json --overwrite
```

### Restore (atomic `.bak` rollback and redo)

Every validated edit applied by `config_patch` (`ask`) or `agent promote` writes a `<config.json>.bak` backup. Use `restore` (or `restore` / `undo` in `sphinxsim shell`) to atomically swap `config.json` and `<config.json>.bak`:

```bash
sphinxsim restore case.json --dry-run
sphinxsim restore case.json
sphinxsim restore case.json --root /workspace --json
```

Running `restore` a second time swaps the two files again (acting as redo).

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

### Ask (agentic CLI turn)

Execute a multi-step agentic turn grounded on SPHinXsim's in-process CLI/MCP operations (`templates`, `template_init`, `config_read`, `config_patch`, `config_restore`, `search_docs`, `validate`, `geometry`, `submit`, `job_wait`, `job_*`, `results_summary`):

```bash
sphinxsim ask "Set end_time to 0.2 s and validate the configuration" -c case.json
sphinxsim ask --dry-run "Preview changing particle_spacing to 0.005" -c case.json
sphinxsim ask "/mcp templates {}" --json
```

Options:
- `-c FILE` / `--config FILE`: Active JSON configuration file (even if syntax- or schema-broken, allowing `config_read` + `config_patch` repair)
- `--output-dir DIR`: Active simulation output directory
- `--root DIR`: Explicit workspace root
- `--dry-run`: Code-enforced read-only mode (blocks mutating tools and forces `dry_run=True` on `config_patch` / `config_restore`)
- `--json`: Return structured `{ "ok": true, "data": { "answer": ..., "observations": ... } }` output

Notes:
- Malformed or schema-invalid tool selections are automatically retried up to two times with schema-repair feedback.
- Inline base64 image payloads are stripped from text observations, while rendered PNGs (`results_summary` with `screenshot` or `results_render`) are forwarded as multimodal `ChatAttachment` images to vision-capable models (falling back automatically to text-only on `mock` or text-only models).

### Run

Execute a validated simulation:

```bash
sphinxsim run config.json
sphinxsim run config.json --output-dir runs/run_01
sphinxsim run config.json --detach --json
```

This:
1. Validates the config
2. Builds SPHinXsys simulation components in C++
3. Runs the simulation
4. Saves output to `--output-dir` (or `.build-temp/test_simulation` by default)

### Results

Inspect saved VTP particle frames, observer and energy `.dat` time-series files, and output quality checks—or render off-screen VTK screenshots, ray-traced **Blender Cycles** images (`--renderer cycles`), `.blend` project files (`--save-blend`), or portable Cycles scene bundles (`--save-bundle`):

```bash
sphinxsim results config.json --output-dir runs/run_01
sphinxsim results config.json --output-dir runs/run_01 --json
sphinxsim results config.json --output-dir runs/run_01 --screenshot pressure.png --color-by Pressure --frame latest
sphinxsim results config.json --output-dir runs/run_01 --renderer cycles --screenshot cycles_vel.png --color-by Velocity --samples 128
sphinxsim results config.json --output-dir runs/run_01 --renderer cycles --fluid-surface --screenshot water.png --save-blend scene.blend
sphinxsim results config.json --output-dir runs/run_01 --save-bundle ./cycles_bundle --color-by Velocity
```

### Preview

Render an interactive 3-D geometry/BC preview before running the solver:

```bash
sphinxsim preview config.json
sphinxsim preview config.json --watch --watch-output-dir runs/run_01
```

This:
1. Validates the config
2. Attempts to invoke `buildGeometries()` from the C++ extension to produce accurate VTP meshes
3. Opens an interactive PyVista window with colour-coded bodies, oriented boxes, and annotations

Options:

| Flag | Description |
| --- | --- |
| `--with-particles` | Also run particle generation and overlay the latest generated particles per body. Hides regular shapes; keeps oriented boxes and annotations. |
| `--watch` | Watch `config.json` (and `--watch-output-dir`) on disk and automatically refresh when external edits or new VTPs arrive. |
| `--watch-output-dir DIR` | Stream VTP frames from `DIR` into a `--watch` preview window. |
| `--off-screen` | Render off-screen (no window) — useful for automated testing |
| `--screenshot FILE` / `-s FILE` | Save a screenshot to FILE instead of opening a window. Implies `--off-screen`. |
| `--renderer {vtk,cycles}` | Choose `vtk` (PyVista, default) or `cycles` (Blender Cycles ray-traced rendering). |
| `--samples INT` | Ray-tracing samples per pixel for `--renderer cycles` (default: `64`). |
| `--resolution WxH` | Output resolution for Cycles renders, e.g. `1920x1080` (default: `1600x900`). |
| `--fluid-surface` | Reconstruct smooth refractive fluid meshes via Blender Geometry Nodes for `fluid_bodies`. |
| `--transparent-bg` | Render Cycles output with a transparent alpha background and ground shadow catcher. |
| `--no-overlay` | Omit the 2D Qt scientific HUD (colorbar, physical time, legend) on Cycles PNG outputs. |
| `--save-bundle DIR` | Export a portable Cycles Scene Bundle (`scene.json` + `.bin` buffers) without requiring Blender. |
| `--save-blend FILE` | Save the constructed `.blend` project file via Blender. |
| `--blender PATH` | Override the Blender executable path (defaults to `$SPHINXSIM_BLENDER_EXECUTABLE` or `PATH`). |

Requires the optional `[visualization]` extra:

```bash
pip install sphinxsim[visualization]
```

The extra includes `pyvistaqt` for responsive persistent shell preview.

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
> preview --with-particles  # overlay generated particles (hides shapes)
> preview --screenshot preview.png   # save a screenshot for a report
> run
> exit
```

### Example 6: Geometry edit loop in shell

```bash
sphinxsim shell
> load config.json
> run
> update "water flow with 5 mm resolution"
> validate
> preview
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

### Example 8: CLI-first template, watch, run, results, and restore workflow

```bash
sphinxsim shell
> templates
> init 2d/column_collapse case.json
> preview --watch
> ask --dry-run "Change end_time to 0.05 s"
> ask "Change end_time to 0.05 s and validate"
> run --output-dir runs/collapse_01
> results --output-dir runs/collapse_01
> undo
> exit
```

### Example 9: High-quality rendering and portable HPC bundle export with Blender Cycles

```bash
# On a workstation with Blender installed (PATH or SPHINXSIM_BLENDER_EXECUTABLE):
sphinxsim results case.json --output-dir runs/collapse_01 \
  --renderer cycles --screenshot collapse_vel.png \
  --color-by Velocity --samples 128 --resolution 1920x1080

# Smooth Geometry Nodes fluid surface + .blend project file:
sphinxsim results dambreak.json --output-dir runs/dambreak_01 \
  --renderer cycles --fluid-surface --transparent-bg \
  --screenshot water.png --save-blend dambreak.blend

# On a headless compute node without Blender or PyVista installed:
sphinxsim results case.json --output-dir runs/collapse_01 \
  --save-bundle ./cycles_bundle --color-by Velocity
```

## LLM provider selection

By default, `sphinxsim` uses a local mock LLM that works offline. To use a different provider:

### Use Ollama (local LLM inference)

```bash
export SPHINXSIM_LLM_PROVIDER=ollama
export SPHINXSIM_LLM_BASE_URL=http://localhost:11434
export SPHINXSIM_LLM_MODEL=qwen2.5:3b
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
export SPHINXSIM_LLM_API_KEY=sk-...
export SPHINXSIM_LLM_MODEL=gpt-4
sphinxsim generate "water dam break"
```

### Use NVIDIA NIM (OpenAI-compatible API)

```bash
export SPHINXSIM_LLM_PROVIDER=nvidia_nim
export SPHINXSIM_LLM_API_KEY=nvapi-...
export SPHINXSIM_LLM_MODEL=meta/muse-glimmer-30b
export SPHINXSIM_LLM_BASE_URL=https://integrate.api.nvidia.com/v1
sphinxsim generate "water dam break"
```

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
