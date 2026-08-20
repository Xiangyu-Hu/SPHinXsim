# AgentCAE

AgentCAE is a durable orchestration layer built on top of the existing SPHinXsim
`generate`, validation, and `run` commands. It does not replace the C++ solver or
the existing shell. It plans a bounded workflow, records every candidate case and
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

The LLM never executes the solver and does not directly edit files. Replanning is
limited to allow-listed actions such as retrying a transient failure, revising
bounded parameter limits, or stopping. Declarative extractors permit only final
ASCII VTP `Position` min/max reductions. Python code, expressions, imports, shell
commands, and arbitrary file paths are forbidden by schema.

## Durable calibration lifecycle

Select the LLM exactly as for the existing `generate` command. AgentCAE uses the
same setting for planning, configuration generation, failure replanning, and
extractor planning:

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"       # deterministic local evaluation
# $env:SPHINXSIM_LLM_PROVIDER = "ollama"   # local Ollama
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
```

Each approval advances at most one proposed solver candidate before the next
approval. A report is generated from persisted workflow truth and contains the
target, tolerance, all parameter/metric observations, best result, artifact IDs,
replanning history, timing, and error state.

## Safe result extractors

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
  store/workflows/<workflow-id>/workflow.json
  store/workflows/<workflow-id>/cases/
  store/tasks/<task-id>/
  extractors.json
  reports/<workflow-id>.json
```

Do not delete or manually change these files while a workflow is active. Use a
separate workspace for independent AgentCAE deployments. The current subprocess
adapter serializes solver use in one workflow; production scheduling of concurrent
GPU/C++ runs remains an infrastructure concern outside the workflow contract.

## Acceptance tests

Fast product and regression tests, excluding opt-in external/real-solver suites:

```powershell
python -m pytest -q -m "not integration"
```

Real C++ calibration is opt-in because it performs several simulations:

```powershell
$env:SPHINXSIM_RUN_REAL_CALIBRATION = "1"
python -m pytest tests/test_real_calibration_workflow.py -q -k mock_llm
```

The real acceptance criterion is convergence to `0.2 m` with an absolute runout
extension error no greater than `0.005 m` within eight unique candidates.

## Current scope

The lifecycle and safety architecture is reusable, but the shipped optimization
workflow is deliberately narrow: one friction-angle parameter and one runout
objective. New simulation families require registered deterministic parameter
tools and metric extractors before an LLM may select them. This preserves a clear
rule: the model chooses among declared capabilities; Python and C++ own validation,
execution, numerical extraction, persistence, and safety limits.
