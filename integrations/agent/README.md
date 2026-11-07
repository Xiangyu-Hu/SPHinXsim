# SPHinXsim agent integration

An installable, host-neutral Python package for bounded CAD generation, native
configuration admission, immutable simulation execution, output verification and
an optional shared native desktop. It contains no tenant, License or chat store.
The native SPHinXsim package (and compiled bindings for actual execution) must be
installed separately. Importing the integration does not load Qt or start a GPU.

## Install and discover

For the recommended container setup, exact image build chain, MCP client JSON
and companion Skill installation, see the [MCP usage guide](MCP_GUIDE.md).

```sh
python -m pip install './integrations/agent[mcp,export,cad]'
sphinxsim-agent --root /path/to/workspace tools
sphinxsim-agent --root /path/to/workspace capabilities
sphinxsim-agent --root /path/to/workspace mcp
```

Use `desktop` extras in the desktop Python environment. CAD can use a separate
Python environment selected through `SPHINXSIM_CAD_PYTHON`; install this package
and CadQuery there. No private registry, account or fixed proxy is required by
the package. The desktop build requires an explicitly supplied native base.

A standard MCP stdio client launches the last command and uses initialize,
tools/list and tools/call. Tool arguments and results are JSON, with structured
results and `isError` failures. `sphinxsim://schemas/contracts-v1` is an actual
readable MCP resource. No HTTP MCP listener is exposed. The tested SDK is
mcp 1.30.0, negotiating protocol 2025-11-25; this is not a claim to implement all
extensions of the latest specification. Request cancellation/disconnection does
not cancel persistent jobs. Explicit `job_cancel` does.

CLI operations use kebab-case names and `--input arguments.json` (or `--input -`
for stdin). For example `validate` takes `{"entry":"case/config.json"}`;
`submit` takes that entry plus `kind: geometry|particles|run`, optional
`leaseSeconds`, `refreshSeconds` and `wallSeconds`. `job-status`, `job-logs`,
`job-continue`, `job-cancel` and `results-inspect` take `{"jobId":"<UUID>"}`.
`geometry-generate` takes `{"recipe":...}`; see capabilities for operations.
The CLI prints `{ok,data}` or `{ok:false,error:{code,message}}` and exits nonzero
on failure. CLI and MCP share the same service, validation and executor.
For CLI desktop operations the owner must supply `--automation-scope <session>`
before the command. Reuse that value across invocations, including Stop; after
Stop choose a new value for a new automation session. MCP creates its own scope
per connection. These scopes fence automation, not human input or solver jobs. Stop also fences
currently active desktop automation, matching the shared native Stop action.
MCP desktop screenshots use standard image content blocks, not base64 in JSON text.

## Execution and persistence

The owner selects a workspace root at process startup, not in tool arguments.
Inputs are relative paths; symlinks, traversal and external resource references
are rejected. Each submission freezes allowed files from the entry directory to
`.sphinxsim/jobs/<UUID>/project`, validates hashes and the native configuration,
and launches a detached supervisor. Keep each experiment in its own directory.
Limits remain 128 files, 512 MiB per asset, 2 GiB total, entry/arguments 1 MiB.
MCP exposes verified artifacts through `sphinxsim://jobs/{jobId}/artifacts/{name}`
resources (1 MiB per read); larger assets require owner-managed file delivery.
Quality/body summaries exceeding 16 KiB are replaced by explicit counts and references.
Generated geometry is delivered by verified artifact references, never inlined
as STL/VTP into model context. File URIs refer to this local stdio host, not a
remote download API. CAD does not implicitly add material, motion or bodies.

The single-machine executor admits at most four active jobs and enforces a
wall-clock deadline. CPU, memory and GPU isolation belongs to the process/container
owner; this package does not claim cgroup or hard VRAM isolation. Heartbeat loss
is `interrupted`, not successful completion or permission to replay a solver.
Unconfirmed interrupted jobs still consume admission capacity. SIGTERM/INT clean
up the owned child group; SIGKILL or host loss needs operator reconciliation.
Linux/macOS local execution uses POSIX locking/process groups; Windows is not
certified. It is not a distributed scheduler.

Particle jobs retain their native instance until the lease expires. Continue
writes one fixed attempt-bound command; duplicate requests do not launch another
process. Expiry retains verified output. Restart by explicitly submitting the
frozen configuration, never a changed draft. Failures preserve verified partial
output. Complete success requires normal native return and verified frames.

For external schedulers, `python -m sphinxsim_agent.runner request.json` uses the
same `/work/project`, control and published-result contract; see `schemas` in the
installed package. Optional `solverDefaults` are supplied by a host and do not
replace values explicitly present in the native JSON. Standalone mode keeps the
native defaults. Project/attempt identity metadata is optional host provenance;
no platform-specific session is needed. The external host owns cancellation,
resource budgets, placement, reconciliation and notification.

## Contracts and quality

MCP is the standard transport. The CAD recipe, project manifest, job, embedding
and quality formats are versioned **SPHinXsim domain contracts**, not industry
standards or the CadQuery native interchange format. Native physical JSON remains
the only physics model. Unknown/known-inactive fields are rejected; source routing
is `wired_unverified`, or `not_inspected` when source is unavailable, never a
certified physical combination. See [agent guidance](AGENT_GUIDE.md).

Hash-verified reports describe decoded saved frames and explicitly list checks
not evaluated. Zero findings cannot establish no leakage, convergence or
physical accuracy. The new `effectiveConfigurationIdentity` defines the hashed
object, encoding, algorithm and version consistently across admission and quality.
Legacy `configurationHash` encodings are unchanged and must not be compared
across report types. Existing output manifest version 2 remains readable.

## Native desktop and reproducible builds

See [desktop](../../docker/desktop/README.md) for local/external host modes, shared operation,
exact embedding origins and optional Computer Use backend. The native renderer
continues to use existing upstream editor/visualizer internals; this is a known
upstream extension seam, not a claim that those private APIs are stable.

```sh
docker build --platform linux/amd64 \
  --build-arg SOLVER_IMAGE=<your-native-sphinxsim-image> \
  -f docker/agent/Dockerfile -t sphinxsim-agent:0.1.0 .
bash docker/desktop/build.sh \
  --build-arg SOLVER_IMAGE=sphinxsim-agent:0.1.0
```

Build the base using the included [native NVIDIA recipes](../../docker/nvidia/build/README.md).
It must provide `/opt/venv`, the native package/bindings and
`/usr/local/bin/sphinxsim-entrypoint`; no prebuilt community image is published
by this change. The Agent image installs MCP/export dependencies plus a separate
CadQuery environment, and the desktop derives from that Agent image. Apt/pip mirrors and `DOWNLOAD_PROXY` are explicit deployment
options; checksums of KasmVNC/VirtualGL stay mandatory. The desktop checks for the
two native editor hooks (`is_dirty`, `discard`) and fails clearly if missing; it
no longer copies a different whole `cli.py` over the installed version. A minimal
upstream hook is included in `sphinxsim/cli.py`. Retain third-party notices and audit
distribution obligations for KasmVNC patches, Qt, OCCT and optional drivers.

## Verification

```sh
PYTHONPATH="$PWD" python -m unittest discover -s integrations/agent/tests -p 'test_*.py'
QT_QPA_PLATFORM=offscreen PYTHONPATH="$PWD" python integrations/agent/tests/test_presentation.py
```

The first command requires the package with mcp/export/cad extras. A dedicated CI
workflow runs it; it does not build the C++ solver. Tests cover actual MCP stdio,
CLI admission, asynchronous real CAD, disconnect persistence, corrupt hashes,
VTP export, edit conflicts and Stop. Qt checks are editor boundaries, not GPU or
remote desktop E2E. Native bindings, H20 execution and a host application's full
flow must be certified separately. The existing numerical solver and examples
are not reimplemented here; high-particle-count stress runs use explicit spacing,
not a changed upstream default fixture.
