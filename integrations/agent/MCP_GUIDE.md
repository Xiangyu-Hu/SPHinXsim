# Connect a general-purpose Agent through MCP

This guide uses the public **stdio MCP** interface. No platform account, private
registry, web backend or distributed Worker is required. Docker is the recommended
way to keep the native bindings, SYCL/CUDA runtime, CAD and optional desktop in a
matched environment; an independently provisioned native Python installation can
also run the same package. The domain formats are SPHinXsim contracts; only the
MCP transport is a general interoperability standard.

## 1. Runtime and image roles

Use a Linux x86_64 host with Docker, an NVIDIA GPU, a compatible NVIDIA driver and
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
CUDA 13 requires an R580-or-newer driver supporting the selected GPU. The supplied
recipes target RTX 3090 (`sm_86`) or H20 (`sm_90`), not every NVIDIA GPU. Other
architectures need a separately qualified recipe. Windows execution and ARM
emulation are not certified. The host does not need its own CUDA SDK or oneAPI.

| Layer | Local tag used below | Contents |
| --- | --- | --- |
| Native solver | `sphinxsim:h20-cuda13` | Native Python package, compiled 2D/3D bindings, oneAPI/SYCL and CUDA adapter |
| Agent executor | `sphinxsim-agent:0.1.0` | Solver plus CLI/MCP, immutable local jobs, VTP verification and a separate CadQuery environment |
| Optional desktop | `sphinxsim-desktop:0.1.0` | Agent executor plus native Qt/PyVista window, KasmVNC, VirtualGL and private application commands |

These are **local build tags**, not published pullable releases. Use your own
registry/tag or image digest when distributing them, and record the source commit
and `docker image inspect` IDs. No community registry publication is implied.
The desktop image does not include a Computer Use provider binary. Application
commands work without one; visual observation/input through the `desktop` tool
requires a separately supplied compatible driver (see [desktop contract](../../docker/desktop/README.md)).

## 2. Build a matched stack

From a full source checkout containing `sphinxsim/sphinxsys/`, on a Linux x86_64
Docker build host, with Bash and Python 3.10+:

```bash
# Run from the repository root. The context directory must not exist and must
# be outside the source tree. This copies the supplied source, including edits.
bash docker/nvidia/build/build-from-source.sh h20-cuda13 \
  "$PWD" "$HOME/sphinxsim-h20-context"

docker build --platform linux/amd64 \
  --build-arg SOLVER_IMAGE=sphinxsim:h20-cuda13 \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  -f docker/agent/Dockerfile -t sphinxsim-agent:0.1.0 .

# Optional desktop; its base must be the Agent executor, not the raw solver.
DESKTOP_IMAGE=sphinxsim-desktop:0.1.0 \
  bash docker/desktop/build.sh \
  --build-arg SOLVER_IMAGE=sphinxsim-agent:0.1.0
```

For RTX 3090, replace the native target/tag with `3090-cuda13`. For CUDA 12,
read the [native build guide](../../docker/nvidia/build/README.md): the exact old
Codeplay installer must be acquired from its vendor and supplied via
`PACKAGE_CACHE`; it is not committed. Public archives are size/hash checked.

Native compilation defaults to 8 jobs, 8 CPU quota and 24 GiB memory; allow at
least 50 GB free disk for one target and its intermediate layers. The wrapper
uses Docker's classic builder; a BuildKit/buildx alternative is in the native
build guide. Compilation needs no GPU. Building needs access to NVIDIA images,
Ubuntu, Intel, PyPI and release archives. Native recipes currently use USTC
APT/PyPI mirrors; Agent/desktop pip indexes can be selected with
`--build-arg PIP_INDEX_URL=...`, desktop APT with `APT_MIRROR`. Desktop release
downloads support `RELEASE_MIRROR` and explicit `DOWNLOAD_PROXY`; checksums remain
mandatory. No private proxy is hardcoded. APT/transitive packages are not fully
snapshot-locked, so rebuilding does not promise an identical image ID.

After building, verify bindings and GPU access **on the runtime machine**:

```bash
GPU_UUID=GPU-your-selected-device-uuid  # choose with nvidia-smi -L
# Grant only the chosen GPU, not every GPU on a shared host.
docker run --rm --gpus "device=$GPU_UUID" sphinxsim:h20-cuda13 \
  --exec /usr/local/bin/sphinxsim-gpu-check
```

The probe validates runtime availability, not simulation accuracy. Import 2D and
3D native extensions in separate processes to avoid native type-registration
collisions. Do not silently reuse an image built for another GPU architecture.

## 3. Keep an executor alive, then attach MCP

Create a dedicated writable workspace for this trust boundary. The image runs
as UID/GID `10001:10001`. The example below uses a new host directory and a
resource-limited, long-lived executor; the memory/CPU values are examples, not a
budget suitable for every experiment. GPU selection does not impose a VRAM quota.

```bash
mkdir -p "$HOME/sphinxsim-work"
sudo chown 10001:10001 "$HOME/sphinxsim-work"
docker run -d --name sphinxsim-executor --init \
  --gpus "device=$GPU_UUID" --memory 32g --cpus 8 --shm-size 2g \
  --cap-drop ALL --security-opt no-new-privileges --network none \
  --mount "type=bind,src=$HOME/sphinxsim-work,dst=/work" \
  --entrypoint /usr/local/bin/sphinxsim-entrypoint \
  sphinxsim-agent:0.1.0 --exec sleep infinity
```

Use this MCP configuration in a client supporting stdio servers (the configuration
file location and wrapper syntax depend on the client):

```json
{
  "mcpServers": {
    "sphinxsim": {
      "command": "docker",
      "args": [
        "exec", "-i", "sphinxsim-executor",
        "/usr/local/bin/sphinxsim-entrypoint", "--exec",
        "/opt/venv/bin/sphinxsim-agent", "--root", "/work", "mcp"
      ]
    }
  }
}
```

Do not allocate a TTY (`-t`): stdout carries MCP JSON-RPC. The image's default
entrypoint runs a scheduler request file, so do not simply launch the image with
`mcp` as its command. The initialization wrapper above loads the native runtime.
The tested SDK is `mcp==1.30.0`, negotiating protocol `2025-11-25`; this does not
claim all extensions of newer specifications. No public MCP HTTP port is opened.

A client disconnect/reconnect only replaces `docker exec`; detached jobs continue
in the executor and their records live under `/work/.sphinxsim/jobs/`. Reconnect
and query the saved job ID rather than submitting again. Stopping/removing the
executor interrupts live computations; persistent files do not restore a native
process or particle lease after a container restart. There is no distributed
scheduler or automatic replay. Share neither Docker access nor one writable
workspace across mutually untrusted users. The MCP root confines tool paths; it
does not sandbox arbitrary code executed by another tool in the same container.

## 4. Author, validate and run an experiment

Give the Agent the [SPHinXsim Skill](skills/sphinxsim/SKILL.md) and access to the
running server's tools/resources. A natural request can be:

> Model water passing through a tank with staggered baffles. Explain the physical
> assumptions first, show the geometry, then run a short coarse trial and tell me
> which conclusions the available results can actually support.

The Agent should discover the runtime contract rather than guess native fields:

1. `capabilities {}` gives schema/routing evidence and the bounded CAD recipe
   operations; read `sphinxsim://schemas/contracts-v1` for the exact domain contract.
   Use `capabilities {"includeSchema":true}` when the full native schema is needed.
2. `geometry_generate {"recipe":...}` starts bounded CAD. Poll `job_status` with
   the returned `id`; `results_inspect {"jobId":"..."}` returns verified geometry
   references and mesh checks. CAD does not assign a material, body or motion law.
3. Create the native JSON and relative assets in a separate experiment directory.
   **MCP intentionally has no arbitrary file-write/copy tool.** Supply trusted
   workspace file tools (operating on the same container paths), or prepare files
   yourself. `compile` returns configuration/diagnostics without writing it; copy
   a verified CAD artifact into the experiment before referencing its relative
   file name. Large STL/VTP bytes must not pass through model context.
4. `validate {"entry":"experiment/config.json"}` checks native admission.
   `submit {"entry":"experiment/config.json","kind":"geometry"}` also validates
   the project assets and exports actual geometry in an immutable job snapshot.
5. `submit {"entry":"experiment/config.json","kind":"particles","leaseSeconds":600}`
   builds retained particles. Wait for `preview_ready`, then call `job_continue`
   with the same job ID before lease expiry. Repeat continue is idempotent. A
   direct `kind:"run"` submission is also supported. Do not substitute a changed
   draft when the retained instance expires.
6. Poll `job_status` and bounded `job_logs`; inspect verified incremental/final
   output with `results_inspect`. Use `job_cancel` only to explicitly cancel a
   calculation. MCP request cancellation is not solver cancellation.

Each submission freezes its entry directory. Keep unrelated experiments separate;
limits are 128 files, 512 MiB per asset, 2 GiB total and 1 MiB entry/tool arguments.
The local executor admits at most four active jobs and enforces wall-clock
budgets; CPU/memory/GPU isolation is the container owner's responsibility. MCP
artifact resources are hash-verified with a 1 MiB read limit; larger delivery is
an owner-provided file operation. Source routing (`wired_unverified`) and schema
acceptance are not tested physical combinations. Reports describe measured saved
frames and list `notEvaluated` checks; finite values/normal exit do not certify
convergence, conservation, no leakage or physical accuracy. See [workflow and
interpretation rules](AGENT_GUIDE.md).

## 5. Optional shared native desktop

The supplied [Compose probe](../../docker/desktop/compose.probe.yaml) starts standalone XFCE
with a TLS VNC listener published **only on the host's loopback**. The following
is a standalone local setup; multi-user/public deployment needs an authenticated
relay. Never publish private control ports 6902/6903 or the Docker socket.

From the repository root, select a GPU and create a one-line password readable
by UID 10001, then start the probe:

```bash
mkdir -p docker/desktop/.secrets
openssl rand -hex 24 > docker/desktop/.secrets/desktop_password
sudo chown 10001:10001 docker/desktop/.secrets/desktop_password
sudo chmod 400 docker/desktop/.secrets/desktop_password
DESKTOP_IMAGE=sphinxsim-desktop:0.1.0 GPU_UUID="$GPU_UUID" \
  docker compose -f docker/desktop/compose.probe.yaml up -d
DESKTOP_ID=$(docker compose -f docker/desktop/compose.probe.yaml ps -q desktop)
```

Open `https://127.0.0.1:16901` in a browser, accept the local self-signed certificate
only for this owned endpoint, and sign in as `simulation` with that password.
For a remote host, first use an SSH local port forward to its `127.0.0.1:16901`;
never expose that port publicly. This probe has its own project volume and
example 64 GiB/8 CPU budget; adjust for the actual hardware.

For an executable sample, initialize a new probe volume with the checked-in
fixed-body fixture (a plumbing probe, not a validated naval model):

```bash
docker cp integrations/agent/tests/fixtures/fixed-ship.json "$DESKTOP_ID:/work/project/config.json"
docker cp integrations/agent/tests/fixtures/fixed-hull.stl "$DESKTOP_ID:/work/project/fixed-hull.stl"
docker exec -u 0 "$DESKTOP_ID" chown -R 10001:10001 /work/project
# Start exactly one managed native window inside the existing standalone XFCE.
docker exec -d -e SPHINXSIM_DESKTOP_MANAGED=1 \
  -e SPHINXSIM_PROJECT_ENTRY=config.json -e SPHINXSIM_HOST_MODE=local \
  "$DESKTOP_ID" /usr/local/bin/sphinxsim-native
```

Keep the idle CLI terminal separate from this window; do not start a second
preview against the same project. In the MCP configuration from section 3,
replace `sphinxsim-executor` with this desktop container's name/ID and add
`--desktop` before `mcp`. MCP and the managed window then share `/work` and the
same local jobs. Use `desktop {"operation":"status"}` and
`desktop {"operation":"application","command":"screenshot"}` to check the
native window. Application commands support validation, CAS-protected updates,
save, preview, CAD, particles and continue. A minimal parameter edit is:

```json
{"operation":"application","command":"validate"}
```

Read `savedConfigHash` from that response. Then send, for example:

```json
{"operation":"application","command":"update","arguments":{
  "expectedHash":"<savedConfigHash>",
  "changes":[{"path":["solver_parameters","end_time"],"value":0.2}]
}}
```

For native desktop CAD, use command `geometry_generate` with `expectedHash`,
`shapeName` and `recipe`, then query `geometry_status` with `{"jobId":"<id>"}`
using the generated job's returned `id`. This is distinct from the headless CAD job API:
the managed desktop additionally attaches a verified candidate to that named
shape and refreshes the same editor, unless human edits cause a conflict. Body
and material assignments remain explicit. `apply_configuration` takes the
`specification` already reviewed through desktop `compile`, `expectedHash` and
`reviewedHash` equal to that compile report's `configurationHash`. Do not guess
those hashes. `particles` and `continue` use empty `arguments`; wait for the
native status to become ready before continuing.
Human and Agent share the window without a controller handoff.
Preserve unsaved edits on conflicts. The private service listens only inside the
container; direct desktop MCP access must be explicitly enabled by its owner.

Without a Computer Use driver, `observe`/visual `action` are unavailable; do not
pretend a command was a mouse interaction. A driver-enabled derived image must
supply its executable, compatible private-envelope API and license notices as
specified in the desktop contract. Re-observe after resize and before short
visual sequences. Stop fences subsequent Agent desktop actions without canceling
independent solver jobs or human input.

For an embedded deployment, use managed startup and a host-owned authenticated
relay with the exact-origin/version/generation [embedding contract](../../docker/desktop/README.md).
Standalone XFCE's local port binding is not a substitute for that boundary.

## 6. Install the companion Skill and verify

The [Skill directory](skills/sphinxsim) is a repository asset, not automatically
installed by pip or MCP discovery. Copy that complete directory to your Agent's
supported skill location, or load its `SKILL.md` and referenced files explicitly.
For Codex, for example:

```bash
mkdir -p "$HOME/.codex/skills"
cp -R integrations/agent/skills/sphinxsim "$HOME/.codex/skills/"
```

The Skill carries its own workflow reference so copying it does not break links.
Other clients may inject the same guidance through their supported instruction
mechanism. It grants no new tool, file or deployment permission.

Protocol/CAD and container-helper checks (no native GPU certification):

```bash
python -m pip install './integrations/agent[mcp,export,cad]'
PYTHONPATH="$PWD" python -m unittest discover -s integrations/agent/tests -p 'test_*.py'
python -m unittest discover -s docker/nvidia/build/tests -v
```

To qualify a newly built stack, additionally run GPU probe, geometry/particle/
continue/verified-result and shared-desktop tests on the target host. Local
protocol or offscreen Qt tests do not establish remote GPU desktop E2E.
Keep original copyright and dependency notices when redistributing; the source
build license does not replace NVIDIA, Intel, Codeplay, KasmVNC, Qt or OCCT terms.
