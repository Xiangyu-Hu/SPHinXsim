# Native CLI and external agents

SPHinXsim uses its existing `sphinxsim` package and CLI for both people and
agents. MCP exposes the same application operations. There is no separate
agent package, command-line program, required cloud service or container image.

For client configuration and resource access, see [MCP setup](mcp.md). The optional
[companion skill](https://github.com/Xiangyu-Hu/SPHinXsim/blob/main/.github/skills/sphinxsim/SKILL.md) follows the repository's
`.github/skills/` convention. Install the complete `sphinxsim` skill directory,
including its `references/`, through your agent client's supported mechanism.
It guides experiment design and quality interpretation; it grants no access.

## Installation

Follow the platform [installation guide](installation.md) to build the native
bindings. From this checkout, install the optional features you need:

```sh
python -m pip install '.[dev,cad,mcp,results,visualization]'
sphinxsim --help
python -m sphinxsim --help
```

`cad` installs CadQuery/OCCT and mesh validation dependencies. `results` supplies
VTK and mesh boolean support for headless export and quality checks. `mcp`
installs the official MCP Python SDK. `visualization` supplies the native Qt
window. These extras do not replace the compiled SPHinXsim bindings. Basic
CLI/schema use does not import CAD, MCP or Qt until requested.

Detached local jobs use native process supervision on Windows and POSIX.
Windows workers use hidden console processes and process-identity checks for
cancellation. A remote desktop, GPU runtime, display/input provider, authentication
gateway and container packaging are the deployment owner's responsibility and
are not shipped in this change.

## Configuration and action authority

Native simulation JSON defines what to simulate. Explicit CLI/MCP operations
define what to do. Validation and execution do not rewrite source JSON.
Compilation returns a new native configuration, unit conversions and normalization
diagnostics; review those before explicitly applying it.

File references resolve within the workspace chosen by the owner. A frozen
project includes the entry JSON and the complete admitted resource inventory,
including sizes and SHA-256 hashes. A hash alone is not a substitute for the
referenced bytes. Raw file hashes and normalized configuration identities have
different scopes and must not be substituted for one another.

Use native snake_case JSON keys and the existing positional-file and
`-o/--output` CLI conventions. Structured operations accept bounded JSON input
and return `{ "ok": true, "data": ... }` or a JSON error. `--json` selects
machine output; native execution diagnostics go to stderr or bounded job logs.

## Configuration and CAD

```sh
sphinxsim capabilities --json
sphinxsim validate project/config.json --root /workspace --json
sphinxsim compile specification.json -o reviewed-config.json --json
sphinxsim geometry recipe.json -o project/models/candidate --json
```

Compilation accepts `{ "version": 1, "configuration": <native configuration> }`.
Within that structure an explicitly supported quantity may use, for example,
`"particle_spacing": { "value": 25, "unit": "mm" }`. Compilation produces
ordinary native numeric JSON; it does not introduce a second physics language.
Combining explicit quantities with native characteristic scaling is rejected.

A minimal CAD recipe is:

```json
{
  "version": 1,
  "unit": "mm",
  "operations": [
    {"id": "body", "op": "box", "args": {"size": [2000, 1000, 800]}}
  ],
  "output": "body"
}
```

Query `capabilities.modeling` for the bounded operations and parameters,
including sketches, extrusion, revolution, loft, sweep, booleans and transforms.
CAD accepts data, never arbitrary Python or shell code. It outputs meter-based
`model.stl`, `model.step`, `recipe.json` and `cad-result.json`, with bounds,
volume, mesh checks and file hashes. Existing output directories are preserved.
CAD success does not define a material, boundary condition, motion law or physical
body: attach the verified geometry through an explicit native configuration edit.

For long CAD work, `geometry recipe.json --detach --root /workspace --json` returns a job
reference immediately. The MCP `geometry` operation also defaults to detached;
set `detach: false` and an explicit workspace-relative `output` for synchronous
generation. Both paths use the same CAD implementation and validation.

## Persistent execution

```sh
sphinxsim project project/config.json --root /workspace --json
sphinxsim preview project/config.json --with-particles --detach --root /workspace --json
sphinxsim run project/config.json --detach --root /workspace --json
sphinxsim job status reference.json --root /workspace --json
sphinxsim job continue reference.json --root /workspace --json
sphinxsim job results reference.json --root /workspace --json
```

`--json`/`--detach` select headless job publication for run/preview. For the
original interactive or off-screen screenshot workflow, keep using native
`preview` without those options; incompatible GUI/job option combinations are
rejected rather than silently ignored.

Save the returned `data.reference` object as `reference.json`:

```json
{"job_id": "<UUID>", "request_sha256": "<SHA-256 of the frozen request>"}
```

Every later job command requires that complete reference. Results, status, logs,
continue and cancel do not infer a current or latest job. MCP submission requires
the reviewed inventory from `project_inspect`; changed inputs are rejected before
execution. Use a stable `submission_id` (CLI: `--submission-id UUID`) to retry the
same frozen submission; reusing it with different inputs or policy is rejected.

Particle preview retains one native instance until its explicit lease expires.
`job continue` continues that instance once; retries do not start another solver.
An expired lease requires an explicitly requested new execution of frozen inputs.
Disconnection and stopping agent operations do not cancel calculations. Explicit
`job cancel` requests cancellation from the live supervisor, never a stored PID.
Interrupted supervisors are reported; inputs are not automatically replayed.

An external scheduler can execute an already-frozen request without creating a
second local queue:

```sh
sphinxsim run --request request.json --output /execution --json
sphinxsim preview --request particle-request.json --output /execution --json
```

The work directory must contain `project/` matching the JSON inventory.
`run` accepts kind `run`; `preview` accepts `geometry` or `particles`.
The request schema is `execution` in
[`contracts-v1.json`](../sphinxsim/runtime/schemas/contracts-v1.json).
The caller owns resource isolation, supervision and a fresh execution directory.
Retained particles accept the explicit `control.json` command bound to their
`attempt`; the work directory and request identify all inputs. Reusing an existing
solver output is rejected. No arbitrary executable is accepted.

A trusted Python host can call `sphinxsim.cli.main(argv, publish_json=callback)`
for frozen `--request` execution. The callback receives `(path, document)` before
manifest/status JSON becomes visible and must publish atomically or raise.
The default is the native atomic JSON writer. This permits an external host to
translate its wire envelope without a second output tree or a race after
publication. There is no CLI/MCP argument that loads executable callbacks;
only trusted embedding code can supply one. Resource hashing, numerical buffers
and native configuration admission remain the native implementation's responsibility.

## Native desktop

```sh
sphinxsim desktop serve project/config.json --root /workspace \
  --reference-file /workspace/.native-desktop.json --locale en --theme dark
```

This opens the native Qt editor and simulation actions. Preview and particle
generation are explicit actions; opening a window does not submit a computation.
The new reference file is owner-readable only and contains a short-lived
authenticated loopback connection. Treat it as a credential. The server binds
an ephemeral loopback port, not a public listener. Closing the window releases
the listener and leaves independent jobs running.

CLI `desktop status request.json --desktop-reference ... --json` reads current
state; `{}` is sufficient for status. Mutating commands name the observed
`instance_id`, `version` and an `automation_id`. Applying or saving uses the raw
`expected_hash`; preview/particles also require a stable `submission_id` UUID.
`apply` accepts a complete reviewed native `configuration`. CAD generation only
produces a candidate: applying it is a separate explicit configuration operation.

Human edits invalidate old versions. Unsaved edits are preserved on conflicts.
`desktop stop` fences the named automation scope, without canceling jobs or
blocking human editing. A fresh turn observes again and uses a fresh scope.
Continue/cancel/result require an explicit job reference. Historical result views
show the frozen configuration and latest verified output; `edit` explicitly
returns to the source configuration. Refresh failures retain the previous scene.
Screenshots capture the native window as PNG on a supported display.

Remote mouse/keyboard, focus observation, resolution changes, video streaming,
browser embedding and provider-specific recovery belong to external integrations.
They are not simulated by the native application commands.

## Results and limits

Published manifests, buffers and original VTP files are verified before exposure.
Only complete VTP snapshots are decoded; unfinished files are retried. Results
can remain available after a failed execution and are marked partial when incomplete.
Bodies can be fluids or continua; counts may vary per frame and fields are taken
from actual output. `results_inspect` returns bounded summaries and verified
resource references, not every frame in the model context.

Quality reports inspect saved outputs: finite data, output time coverage,
particle counts, supported domain checks and penetration into supported static
solid geometry. They state tolerances and `not_evaluated` checks. Open boundaries,
native scaling, missing surfaces and insufficient data restrict interpretation.
Source routing evidence, schema admission, successful execution and physical
accuracy are different claims. No report certifies convergence or all coupled
physics combinations; global extrema do not locate a pressure hotspot.

## Tests

Use the existing project build, install and test entry points:

```sh
python -m pip install -e '.[dev,cad,mcp,results,visualization]'
pytest tests/ -v
ctest --test-dir build-integrated --output-on-failure -L simulation_class
```

The native Linux CI installs the extras and runs real OCCT, stdio MCP, native
particle/continue/lease and J2 material-builder tests. It also runs an opt-in
real Qt/VTK window and PNG test under Xvfb with a CPU renderer. Without bindings,
local native tests skip explicitly; `SPHINXSIM_REQUIRE_NATIVE_TESTS=1` turns that
absence into failure. `SPHINXSIM_DESKTOP_TESTS=1` enables the real-window test
when a display and bindings are available. Qt editor tests alone do not establish
GPU or remote-desktop end-to-end behavior.
