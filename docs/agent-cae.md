# AgentCAE

AgentCAE is a durable orchestration layer using the native SPHinXsim MCP stdio
service. It discovers the running tool inventory, plans a bounded workflow, records every candidate case and
result, pauses before solver execution when approval is enabled, analyzes VTP
artifacts, and adjusts the next candidate until the requested tolerance is met.

The current validated workflow calibrates the friction angle of a two-dimensional
granular column collapse to a `runout_extension` target of `0.2 m`.
`runout_extension` means final front minus initial front; it does not mean the
absolute final x-coordinate.

## Control and execution boundaries

AgentCAE separates uncertain reasoning from deterministic execution:

1. A structured planner proposes a workflow or analysis definition as JSON.
2. Pydantic schemas and capability registries reject unknown tools, fields, units,
   bodies, coordinate components, and reductions.
3. LangGraph controls approval, execution, analysis, search, replanning, and
   terminal states.
4. SQLite checkpoints and workflow artifacts allow another Python process to
   inspect or resume the same workflow without repeating a completed C++ run.
5. SPHinXsim performs configuration generation, validation, and C++ simulation.
6. Deterministic streaming readers extract approved metrics from final VTP files.
7. Before the first solver run, an off-screen particle Preview is reviewed by a
   multimodal model. This visual preflight can accept the case or request at most
   one validated regeneration pass; it never measures numerical results.

The LLM never executes the solver and does not directly edit files. Replanning is
limited to allow-listed actions such as retrying a transient failure, revising
bounded parameter limits, or stopping. Declarative extractors permit only final
ASCII VTP `Position` min/max reductions. Python code, expressions, imports, shell
commands, and arbitrary file paths are forbidden by schema.

## Durable calibration lifecycle

Select the LLM exactly as for the existing `generate` command. AgentCAE uses the
same setting for planning, configuration generation, visual preflight, failure
replanning, and extractor planning. For Ollama, select a model that can consume
images because visual preflight sends a PNG screenshot:

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"       # deterministic local evaluation
# $env:SPHINXSIM_LLM_PROVIDER = "ollama"   # local Ollama + vision model
# $env:SPHINXSIM_LLM_PROVIDER = "openai"   # OpenAI API
# $env:SPHINXSIM_LLM_PROVIDER = "nvidia_nim" # NVIDIA NIM API
```

Start a workflow. With `mock`, this tests the production control path without
sending data to an external LLM:

```powershell
python -m sphinxsim agent --workspace .build-temp/agent start `
  "Calibrate a 2D column-collapse runout extension to 0.2 m." `
  --workflow-id column_020 --json
```

Inspect, approve, resume, cancel, or report from a later process:

```powershell
python -m sphinxsim agent --workspace .build-temp/agent status column_020 --json
python -m sphinxsim agent --workspace .build-temp/agent approve column_020 --json
python -m sphinxsim agent --workspace .build-temp/agent resume column_020 --json
python -m sphinxsim agent --workspace .build-temp/agent cancel column_020 --json
python -m sphinxsim agent --workspace .build-temp/agent report column_020 --json
python -m sphinxsim agent --workspace .build-temp/agent execution column_020
python -m sphinxsim agent --workspace .build-temp/agent events column_020
python -m sphinxsim agent --workspace .build-temp/agent results column_020
```

Approval covers the complete bounded Plan. A durable worker then performs the
visual preflight and all required generate/run/analyze/adjust iterations. A report
is generated from persisted workflow truth and contains the target, tolerance,
visual review evidence, analysis contract, all parameter/metric observations,
best result, immutable attempt logs, replanning history, timing, and error state.

## Safe result extractors

### Parallel Chatbot and Preview windows

When `agent approve` or `agent resume` is sent in Chatbot, the solver runs in its
durable worker and a separate desktop Preview window observes its output. The
window reuses Preview's geometry, model information, gravity, observers, colours,
and particle rendering. It never generates particles or starts a second solver.
The same particle updater is used by the shell's `continue-to-run` display.

The window follows new candidate runs automatically and preserves the camera.
Its **Agent results** menu can switch between the current run, the best completed
candidate, and individual completed candidates. The final frame remains visible
after convergence or cancellation. Closing the window does not cancel the worker.
Send `agent preview` to reopen it. After reopening Chatbot, send
`agent attach <workflow-id>` followed by `agent preview` to observe an existing run.

Preview opens on the computer running Chatbot; a remote browser does not receive
the desktop window. It requires `pyvistaqt` and `PySide6` in that Python environment.
Window process diagnostics are written to
`execution/<workflow-id>/preview.log`. The chat retains progress, iteration results,
and reports instead of appending a live PNG every five seconds. Opening this window
does not change when an LLM receives images: visual preflight still occurs before
the first solve, and live frames remain for user observation.

Run the observer regression and actual Qt window smoke test with:

```powershell
python -m pytest tests/test_agent_preview.py -q
```

For deterministic evaluation, prepare a JSON response that matches the
`DeclarativeExtractorSpec` schema and use the mock structured-planner boundary:

```powershell
python -m sphinxsim agent --workspace .build-temp/agent extractor plan `
  "Measure the final deposit height." `
  --config case.json `
  --response-file extractor-response.json --json

python -m sphinxsim agent --workspace .build-temp/agent extractor list --json

python -m sphinxsim agent --workspace .build-temp/agent extractor analyze `
  deposit_height case.json path/to/run-directory --json
```

The planning command validates `case.json` and derives the body and spatial
dimension allow-lists instead of trusting user-supplied declarations. An extractor
definition is persisted with a SHA-256 fingerprint. It can be
recompiled in another process and applied to a different artifact set with the
same body and dimensional contract. The VTP reader streams numeric content and
stores bounds and counts; it does not build a full in-memory particle array.

When `SPHINXSIM_LLM_PROVIDER` selects a cloud provider, extractor planning sends
the extractor request, listed body names, spatial dimension, allowed operations,
and output schema to that configured endpoint. Only use a cloud provider when
that bounded data export is authorized.

## Persisted data

The workspace contains:

```text
workspace/
  checkpoints.sqlite
  runtime/<workflow-id>.json
  execution/events.sqlite
  execution/<workflow-id>/worker.json
  execution/<workflow-id>/attempts/<attempt-id>/
  store/workflows/<workflow-id>/workflow.json
  store/workflows/<workflow-id>/cases/
  store/tasks/<task-id>/
  extractors.json
  reports/<workflow-id>.json
```

Do not delete or manually change these files while a workflow is active. Use a
separate workspace for independent AgentCAE deployments. The RunManager enforces
one active solver worker per workspace, verifies process identity before stopping
a process tree, and marks orphaned workers as interrupted for explicit resume.

## Upstream configuration compatibility

Agent validation, extractor planning, Workbench loading, proposals, and saves use
the shared native admission checks in `sphinxsim.runtime.physics`. Native CLI and
MCP operations use the same checks. Invalid or inactive material fields therefore
produce an error at the editing or planning entry point.

LLM configuration cleanup preserves named `box` and `bounding_box` primitive
references. Initial-front analysis resolves a named `bounding_box` primitive and
supports inline bounds. It checks reference uniqueness, primitive type, finite
ordered bounds, and direction dimensionality. Preview also infers dimensionality
from primitives when the domain and gravity are omitted.

Use the upstream configuration locations for new or revised cases.

- Set surface handling with `fluid_bodies[].surface_type`.
- Define a container with `polygons[].inner_box`, using inline bounds or a named
  `bounding_box` primitive.
- Set solid refinement with
  `particle_generation.settings.bodies[].solid_body.refinement_to_global`.
  The shared admission check reports `adaptation_ratios` as an inactive field
  and includes the current configuration path in its error.
- Reference a relaxation geometry with
  `particle_generation.settings.bodies[].relaxation.level_set.shape`.

AgentCAE's RunManager owns workflow orchestration. Native MCP `project_inspect`,
`submit`, `job_status`, `job_logs`, `job_cancel`, and `results_inspect` own solver
execution on Windows and POSIX. Each workflow run stores the complete job
reference in `job-reference.json`. Reopening a submitted run queries that job.
Analysis verifies published hashes and uses the frozen configuration and saved
particle sources. Preview rendering uses `results_render` on a published particle
job. Each MCP attempt records its transport, reference, timing, and failure state.

Configuration generation, reviewed editor proposals, case/resource preparation,
friction edits, and result extraction use operations registered in
`sphinxsim.operations.TOOLS`. The official MCP SDK client calls the same service
available to external clients. The planner receives its discovered tool schemas
alongside the calibration contracts and upstream documentation.

The Chatbot discovers native tools for structured LLM tool selection. Real
providers can inspect capabilities, author configurations, generate CAD, submit
jobs, and inspect results from the conversation. An explicit command such as
`/mcp capabilities {}` also works with the mock provider. Embedded editor changes
use `workbench` and `config_propose`, followed by the existing proposal review.

## Acceptance tests

Run these commands from the repository root in the existing project Python
environment. Each stage checks a different part of the Agent workflow.

### Static review

```powershell
git diff --check
git diff --stat
git diff
```

### Configuration and analysis regression tests

These tests exercise Python configuration admission, primitive preservation,
initial-front extraction, planner context, and Workbench document validation.

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"
python -m pytest -q tests/runtime/test_configuration.py tests/test_llm.py tests/test_runout_analyzer.py tests/test_extractor_planner.py tests/test_local_knowledge.py tests/test_planner_context.py tests/test_workbench.py -m "not integration"
python -m pytest -q tests/test_visualization.py -k "TestSpatialDimensionInference"
python -m sphinxsim capabilities --json
python -m sphinxsim validate tests/test_simulation/test_2d_simulation/data/column_collapse.json
```

All selected tests should pass. The configuration tests check equivalent
admission through CLI, MCP operations, and compilation, preserve the source file,
and exercise refinement and named Level Set dictionaries. Runout tests compare
inline and primitive-based initial fronts in 2D and 3D with both direction signs.

### Deterministic Agent workflow tests

The deterministic adapter supplies particle artifacts for the complete
plan/approve/execute/analyze/report cycle. Lifecycle tests also exercise the real
CLI and persisted state with the mock LLM provider.

```powershell
python -m pytest -q tests/runtime/test_agent_tools.py tests/test_mcp_tool_conversation.py tests/test_mock_vertical_workflow.py tests/test_deterministic_calibration_workflow.py tests/test_agent_cae_product_acceptance.py tests/test_agent_cli.py
```

The vertical acceptance test checks four completed candidates, persisted reports,
and convergence. Product acceptance also checks reopening workflows in another
facade and controlled failures for unavailable artifacts.

### Real solver acceptance

Use an environment with the project's existing 2D native extension. This test
executes several C++ simulations with a mock LLM provider.

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"
$env:SPHINXSIM_RUN_REAL_CALIBRATION = "1"
python -m pytest tests/test_real_calibration_workflow.py -q -k mock_llm
Remove-Item Env:SPHINXSIM_RUN_REAL_CALIBRATION
```

The acceptance criterion is convergence to `0.2 m` with an absolute runout
extension error at most `0.005 m` within eight unique candidates.

### Manual lifecycle and desktop checks

Use the existing native extension and visualization dependencies for visual
preflight and solver execution. The `mock` setting supplies LLM responses while
the approved workflow runs the native simulator.

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"
$agentWorkspace = ".build-temp/agent-upstream-validation"
$agentWorkflowId = "upstream_" + (Get-Date -Format "yyyyMMdd_HHmmss")
python -m sphinxsim agent --workspace $agentWorkspace start "Calibrate a 2D column-collapse runout extension to 0.2 m." --workflow-id $agentWorkflowId --json
python -m sphinxsim agent --workspace $agentWorkspace status $agentWorkflowId --json
python -m sphinxsim agent --workspace $agentWorkspace approve $agentWorkflowId --background --json
python -m sphinxsim agent --workspace $agentWorkspace execution $agentWorkflowId
python -m sphinxsim agent --workspace $agentWorkspace events $agentWorkflowId
python -m sphinxsim agent --workspace $agentWorkspace status $agentWorkflowId --json
python -m sphinxsim agent --workspace $agentWorkspace report $agentWorkflowId --json
```

Before approval, expect `awaiting_approval` to be true and `case_prepared` to be
false. After the worker finishes, expect status `converged`,
`best_absolute_error <= 0.005` in status, and
`best_result.absolute_error <= 0.005` in the report. Inspect each attempt's
`stdout.log`, `stderr.log`, and `attempt.json` under
`execution/<workflow-id>/attempts/` if a command fails. Open another terminal with
the same workspace and workflow ID to check persisted status. `cancel` requests
termination, and `resume` continues an interrupted running workflow.

Open `python -m sphinxsim workbench` to check the JSON editor, reviewed LLM
proposals, explicit save, Preview, and Agent chat. Preview should select the 2D
binding for a case whose dimensions are defined only through named primitives.
The optional desktop test exercises the real Qt/chat/preview/solver flow.

```powershell
$env:SPHINXSIM_RUN_WORKBENCH_DESKTOP = "1"
python -m pytest tests/test_workbench.py -q -k real_workbench_desktop
Remove-Item Env:SPHINXSIM_RUN_WORKBENCH_DESKTOP
```

## Current scope

The lifecycle and safety architecture is reusable, but the shipped optimization
workflow is deliberately narrow: one friction-angle parameter and one runout
objective. New simulation families require registered deterministic parameter
tools and metric extractors before an LLM may select them. This preserves a clear
rule: the model chooses among declared capabilities; Python and C++ own validation,
execution, numerical extraction, persistence, and safety limits.
