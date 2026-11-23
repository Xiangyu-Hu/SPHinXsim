---
name: issue-solving
description: "Repository-wide workflow for solving coding issues and carrying verified, reusable lessons into the relevant project skill. Invoke for coding issue investigations and fixes."
---

# Issue-solving workflow

## Purpose

Use this skill for coding issue investigations and fixes across SPHinXsim. It
complements focused skills such as `ci`, `visualization`, and `config`; it does
not replace their domain-specific instructions.

## Workflow

1. Understand the reported behavior and inspect the relevant code, tests, and
   documentation before changing anything.
2. Check `.github/skills/` for a skill covering the affected area. Follow a
   relevant skill's instructions and use its testing guidance.
3. Implement a focused fix that addresses the root cause without unrelated
   changes. Update directly related documentation and tests as appropriate.
4. Verify the behavior with the narrowest meaningful test or check, and report
   what was and was not verified.
   For subprocess-driven desktop tests, check child-process exit status promptly
   and include its diagnostic logs in assertion output. When a worker's workspace
   is outside the project checkout, use its explicit writable workspace as the
   execution root; a common path can unexpectedly resolve to `/`.
5. Before finishing, decide whether the investigation produced a verified,
   durable lesson that would help future work in the affected area:
   - If a relevant skill exists, update it with the concise reusable guidance.
     Do not add temporary details, speculative explanations, or facts already
     covered by the skill.
   - If no relevant skill exists, ask the user whether they want a new focused
     skill before creating one. Do not create a skill automatically.
   - If there is no durable lesson to preserve, leave skills unchanged.

## Boundaries

- Skill updates are for reusable repository guidance, not a changelog of every
  issue or a substitute for tests and code fixes.
- Keep each skill focused. If a lesson belongs to a different area, update that
  area's skill rather than expanding this general workflow.
- Do not overwrite or discard unrelated user changes when updating a skill.
