# SPHinXsim Workbench

The desktop workbench places the PyVista Preview on the left and a shared sidebar
on the right. **JSON Editor** and **Chat** are peer tabs in that sidebar, similar
to switching assistants in VS Code. It uses the existing LLM provider configuration and
AgentCAE worker, checkpoint and result interfaces.

## Start

Use the Python environment containing the built SPHinXsim extensions and the
`chatbot` and `visualization` extras (Chainlit, PySide6, PyVista and PyVistaQt):

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"
python -m sphinxsim workbench
```

The default opens a workspace copy of the two-dimensional column-collapse demo;
it does not edit the repository's demo file. To open your own JSON:

```powershell
python -m sphinxsim workbench path/to/case.json --workspace .build-temp/workbench-demo
```

Existing workspace cases using `container_box.inner_lower_bound` and
`inner_upper_bound` open with an updated `inner_box` in the editor draft.
The **JSON Editor** tab marks this draft as unsaved. Review it and use
**Save + Preview** to persist the updated geometry fields. Cases containing both
representations or an incomplete pair of legacy bounds require an explicit edit.

Switch between **JSON Editor** and **Chat** using the right sidebar tabs. Both
keep their state: switching preserves unsaved edits, editor selection and the
chat conversation. Drag the vertical splitter to resize the sidebar. Preview
remains visible at the full height of the left pane.

The JSON draft and left canvas are independent of the chat server. A local
Chainlit child process is embedded in the **Chat** tab with Qt WebEngine. Startup
errors are recorded in `<workspace>/chat.log`; use **Reconnect chat** to retry.
The interface and embedded Chainlit controls use English.

The canvas reuses `ConfigVisualizer`, live VTP loading, and `AgentPreviewObserver`.
VTK renders offscreen and Qt presents the frames, avoiding native OpenGL child
window issues on Windows while retaining camera interaction. Drag to rotate,
middle-drag to pan, use the wheel to zoom, and press `R` to reset the view.
The left canvas contains the model and results; the Model Information sidebar
is removed from the workbench, so the scene uses the full canvas width.

## Edit and preview

- **JSON Editor** uses the same **Setting / Value** property tree as the original
  shell Preview. Filter settings, expand or collapse groups, edit typed values,
  and add, duplicate, remove or reorder list entries. **Revert** undoes an edit;
  **Reload** reads the saved file after checking for unsaved changes. The shared
  formatter preserves existing field selection, numeric spelling, layout and
  LF/CRLF line endings, following upstream JSON Editor PRs
  [#160](https://github.com/Xiangyu-Hu/SPHinXsim/pull/160) and
  [#185](https://github.com/Xiangyu-Hu/SPHinXsim/pull/185).
- Edit values, then **Validate** or **Save + Preview** (`Ctrl+S`). Invalid edits
  leave the saved file and last valid scene intact. **Preview draft** previews
  valid unsaved edits without saving them.
- **Initial particles** enables particle generation for the next preview.
- In chat, ask about the current JSON or send `/config change end time to 0.08 seconds`.
  The `workbench` and `config_propose` MCP tools produce a validated revision-bound proposal.
  In mock mode this example is deterministic; mock is not a natural-language model.
- A new proposal marks the **JSON Editor** tab with a dot and leaves Chat visible.
  Switch to **JSON Editor > LLM diff**, click **Apply proposal to draft**, then
  **Save + Preview**. A star on **JSON Editor** indicates unsaved draft edits.
  Proposals cannot silently overwrite newer manual edits, and externally changed
  files must be reopened before saving.
- Chat discovers the native MCP tools for configuration, CAD, job control, and
  result inspection. Send `/mcp capabilities {}` to inspect capabilities with a
  mock provider. Real LLM providers can select tools from the discovered schemas.
- **Run saved case** starts an isolated C++ process using a snapshot of the saved
  configuration. **Stop case run** stops that manual run. Runtime logs and VTPs
  are kept below `<workspace>/manual_runs/`.

## Agent demonstration

Send the following in the embedded chat:

```text
agent start Calibrate a 2D column-collapse runout extension to 0.2 m.
agent approve
```

Approval starts the existing durable worker, and the left canvas follows its
VTP output without opening another Preview window. The **Agent results** selector
switches between live, best, and completed candidates. **Displayed run config**
is read-only; switching runs never overwrites the JSON draft.

Use `agent status`, `agent report`, `agent cancel`, or
`agent attach <workflow-id>` followed by `agent preview` to reconnect. The desktop
**Attach** control can also observe an existing workflow in the same workspace.

Agent start still generates an isolated calibration case from its plan; it does
not automatically optimize the editor draft. Use **Run saved case** for the
currently edited model. Only registered AgentCAE workflows are supported.

Closing the window stops its chat server and preview preparation. Durable Agent
workers continue until completion or explicit `agent cancel`. Closing during a
manual run requires confirmation and stops that run.

## Tests

```powershell
$env:SPHINXSIM_LLM_PROVIDER = "mock"
python -m pytest tests/test_workbench.py tests/test_chainlit_chatbot.py tests/test_agent_preview.py -q -m "not integration"
```

Run the actual embedded browser, native Preview, short C++ simulation and Agent
plan smoke test in a desktop session (mock LLM, no external API calls). It also
checks visible Qt canvas pixels, mouse-wheel zoom, and native 3D preview with
mouse-drag rotation:

```powershell
$env:SPHINXSIM_RUN_WORKBENCH_DESKTOP = "1"
python -m pytest tests/test_workbench.py -q -k real_workbench_desktop
```

The desktop test saves screenshots and `probe.json` in its temporary workspace.

The workbench uses existing compiled modules; changing the desktop Python code
does not require rebuilding C++.
