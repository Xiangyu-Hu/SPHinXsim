# Agent workflow for SPHinXsim

Use the tool inventory and the native schema from the running build. Builder
source evidence, schema acceptance, normal solver exit and physical accuracy
are different levels of evidence. Never present them as interchangeable.

1. Describe the experimental goal, units, geometry, materials, boundaries,
   forcing, resolution and the measurements that will answer the question.
2. Query capabilities before selecting a combination. `wired_unverified` is
   source evidence, `not_inspected` means source is unavailable. Neither certifies
   a coupling. Do not silently replace unsupported physics with a similar case.
3. Generate CAD through bounded data operations. Inspect real STL geometry and
   mesh checks. A geometric shape does not create a physical body or motion law.
4. Compile explicit quantities in the native configuration; inspect diagnostics
   and normalizations. Validate before submitting. Do not hide assumptions or
   invent fields accepted only by a loose JSON object.
5. Start with a coarse, short trial. Return the persistent job reference; query
   it when needed. Disconnecting or canceling an MCP request is not permission
   to terminate an independent simulation. Cancel only when explicitly asked.
6. Retained particles may continue only within their lease and frozen input.
   Expiry requires a new execution of that frozen input. Never silently substitute
   a changed draft. Repeated continue must not launch duplicate calculations.
7. Read the quality report and its `notEvaluated` entries. Zero findings does not
   prove no leakage, convergence or accuracy. Saved-frame extrema have no spatial
   locations; do not infer a bow/wake/local pressure from a global scalar range.
8. For the optional shared desktop, observe before short visual action sequences
   and verify the outcome. Human actions may change focus, values or camera.
   Do not reuse stale coordinates after resizing. Preserve unsaved edits on CAS
   conflicts. Stop fences automatic actions, not human input or solver work.

Keep large STL/VTP assets outside model context. Use verified references and
bounded summaries. The deployment owner chooses workspace access and GPU/CPU/
memory limits. This guide does not grant access or replace tool enforcement.
