# Connect an external agent through MCP

Build/install the native project following the [installation guide](installation.md),
then install the required extras from the same checkout:

```sh
python -m pip install '.[mcp,cad,results,visualization]'
sphinxsim capabilities --json
```

Configure an MCP client with the **installed native CLI**, for example:

```json
{
  "mcpServers": {
    "sphinxsim": {
      "command": "/path/to/venv/bin/sphinxsim",
      "args": ["mcp", "--root", "/workspace"]
    }
  }
}
```

The server uses the official SDK and stdio transport. It requires no product
account, cloud gateway or proprietary handshake. The process owner selects the
workspace and supplies resource isolation. Do not expose unrestricted workspaces.

Discover the running inventory instead of copying a stale schema. `capabilities`
reports source evidence and limitations; `compile` returns a new configuration;
`validate` checks an explicit `config_file`; `project_inspect` returns the resource
inventory; `submit` requires that reviewed `project` inventory. Use `submission_id`
for retries. `geometry` defaults to a persistent CAD job. All job operations take
the returned complete `reference` object, including its request hash.

A native desktop is optional. Start it with `sphinxsim desktop serve ...` and give
its owner-only reference file to `sphinxsim mcp --root ... --desktop-reference ...`.
Only then is `desktop` advertised. Observe `status`, then send the explicit
instance/version and operation scope; the server never silently fetches a fresh
version to make a stale edit succeed. Remote computer-use drivers are external.

Verified artifacts use `sphinxsim://jobs/{job_id}/artifacts/{name}` resources. Reads
are limited to published files and 1 MiB per resource; use owner-provided file
delivery for large assets. Resource access rechecks published size/hash evidence.

Read the [native workflow guide](agent-workflows.md) for CLI examples,
JSON contracts, execution lifecycle, quality interpretation and test boundaries.
The optional [skill](https://github.com/Xiangyu-Hu/SPHinXsim/blob/main/.github/skills/sphinxsim/SKILL.md) guides experimental decisions;
install it through your client's supported skill mechanism. It grants no access.

No Docker image is required by this protocol. Image names, GPU devices, remote
desktop servers, browser embedding and authentication are deployment choices
outside this repository's agent implementation.
