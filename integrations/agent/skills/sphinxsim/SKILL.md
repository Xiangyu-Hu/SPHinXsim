---
name: sphinxsim
description: Build, run and interpret SPHinXsim experiments through its MCP tools, including bounded CAD and optional shared native desktop operations. Use for simulation work with a connected SPHinXsim executor.
---

# SPHinXsim experiments

Use the running MCP inventory and native configuration schema. Read
[references/workflow.md](references/workflow.md) before constructing or interpreting
an experiment. These instructions guide decisions; they grant no access.

- Translate the user's question into geometry, units, material, boundary/forcing
  assumptions and measurable outcomes. Ask only for missing choices that change
  the experiment; expose assumptions in ordinary language.
- Discover `capabilities` before selecting physics or CAD operations. Treat source
  evidence, schema acceptance, successful execution and physical validation as
  separate evidence. Do not invent unsupported coupling or silently simplify it.
- Use bounded CAD data, inspect verified artifact references, then attach geometry
  to explicit native bodies/materials through the workspace's file tools or
  managed desktop. MCP has no arbitrary shell/file-write operation. If those
  owner-provided tools are absent, explain the missing step instead of claiming
  that configuration was saved.
- Validate and inspect geometry before a short coarse particle/solver trial.
  Record the persistent job ID, query rather than resubmit after a disconnect,
  and continue only the retained frozen instance within its lease. Return progress
  without blocking the conversation for a full computation.
- Read `results_inspect` and `notEvaluated` before interpreting output. Distinguish
  measured values from hypotheses and unresolved numerical/physical questions.
  Global extrema do not locate a pressure hotspot or prove a wake structure.
- For an enabled shared desktop, observe before short visual sequences, verify
  the outcome, and preserve human edits on conflicts. Resize invalidates old
  coordinates. Application commands must be described as commands, not simulated
  mouse input. Without a compatible visual driver, use the available application
  commands and state that visual input is unavailable.
- Stopping Agent desktop actions or disconnecting MCP does not cancel independent
  computation. Cancel calculations only when the user asks. Do not hide a failed
  solver, incomplete output, expired lease or unverified physical assumption.

Keep large STL/VTP assets out of model context. Deliver bounded summaries and
verified references with human-readable explanations and concrete next steps.
