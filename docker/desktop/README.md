# Shared SPHinXsim native desktop

The optional desktop uses Qt/PyVista, KasmVNC 1.5.0 and VirtualGL 3.1.5. Humans
and agents share the same window. There is no control handover lock. A window
close or automation Stop does not cancel a persistent solver.

`SPHINXSIM_DESKTOP_MANAGED=1` enables the application window;
`SPHINXSIM_PROJECT_ENTRY=config.json` selects an existing validated project entry
under `/work/project`. `SPHINXSIM_WORK_ROOT` can override `/work` for a local
installation. `SPHINXSIM_HOST_MODE=local` (default) implements actual local save,
particle generation, continued execution and verified latest-output mirroring
with the same JobStore as CLI/MCP. Project admission/copying runs off the Qt loop.
`external` leaves intents for a host to acknowledge through its protected
transport; never run both host modes for one desktop.

The internal loopback control socket at 6902 and optional Cua driver socket at
6903 are **private**, not MCP or public APIs. Keep them inside the container's
network/OS boundary. The public stdio server accesses the window only when its
owner starts it with `--desktop`. Computer Use is optional: application commands
work without the driver, while visual actions require a compatible driver.
Drivers are supplied by the deployment with their licenses; no other product's
repository is a build prerequisite for the generic image.

The image binds the managed VNC listener to loopback. A host uses its authenticated
relay; standalone XFCE mode can use `compose.probe.yaml` with a loopback-only host
port. Mount a one-line 16–128 character password at
`/run/secrets/desktop_password`; do not put credentials in query parameters or
images. Select GPU devices explicitly. The renderer defaults to NVIDIA/EGL;
`SPHINXSIM_DESKTOP_RENDERER=software` is diagnostics only.

Application commands retain saved-hash CAS, dirty-edit protection, frozen input,
asynchronous CAD and same-instance continue. A trusted host/stdio process creates
the opaque 64-character `automationId`; the agent cannot choose that identity.
Stop blocks later actions in that scope, including unapplied CAD, without
rolling back delivered inputs or canceling independent computation. Observe
after resize/font/theme changes and before a short visual sequence. Human focus
and values may change at any time. Only the latest verified VTP is shown; no
historical timeline/field visualization is added. Existing read-only result
and camera/previous-valid-view preservation remain.

## Embedding contract version 1

Use `sphinxsim_embed=1`, `sphinxsim_origin=<exact-parent-origin>`,
`sphinxsim_connection=<opaque-id>`, `sphinxsim_generation=<opaque-generation>`.
Messages use `protocol:sphinxsim-desktop, version:1` and both IDs. Parents must
check iframe source and exact origin as well as generation; the iframe accepts
configure messages only from that exact parent. HTTP(S) origins are validated;
other schemes must be explicitly listed by the image owner in
`assets/sphinxsim-embed-policy.js`. The query string cannot add allowed schemes.

A transport connection is not a rendered frame. The pinned Kasm patch reports
first-frame only after decoded framebuffer painting; retains a previous frame
through bounded reconnection and masks input while unavailable. No credentials
cross postMessage. Bundle hash/anchors must still match exactly. The public UI
contract does not make Kasm's private patch anchors or native editor internals
stable. Keep their source/license provenance and certify upgrades separately.

Build from `docker/desktop`, using `build.sh` and an explicit
`--build-arg SOLVER_IMAGE=...` pointing to the Agent image (which includes the
isolated CAD environment), not the raw native solver. See the [MCP setup guide](../../integrations/agent/MCP_GUIDE.md)
for the complete native → Agent → desktop build chain and standalone usage.
Apt/pip mirrors can be passed as build arguments;
release mirror/proxy options only affect downloads and never disable checksums.
The generic image omits a provider binary. To add one, derive an image that
installs the compatible executable at `/usr/local/bin/cua-driver` and includes
its notice; host-specific patches and embedding policy belong in that host.

No new H20 image/desktop E2E has been run for this migration yet. Local Qt/editor,
HTTP boundary and protocol checks do not prove native GPU rendering/solver
behavior after rebuilding. Upgrade host, desktop and solver images as a matched
set after draining old executions and closing old desktop instances; mixed
embedding protocols are intentionally rejected rather than falsely ready.
