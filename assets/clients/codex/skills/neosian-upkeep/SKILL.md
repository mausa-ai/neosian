---
name: neosian-upkeep
description: Review and reconcile durable neosian memory through the connected neosian-memory MCP server. Use when the user asks for neosian upkeep, a review of stored memories, or stale and duplicate memory cleanup.
---

# Neosian upkeep

Use the current Codex conversation to review memory. Load neosian's live
workflow instead of maintaining a separate copy of its instructions here.

1. Find the connected `neosian-memory` MCP tools. Call `list_skills`, then
   `load_skill` with `name: "builtin:upkeep"` to select the shipped workflow.
   Profiles must select `builtin:upkeep`; honor their selection and permissions.
   If the server or skill is unavailable, report the missing connection or
   selection; do not change profile settings or claim upkeep ran.
2. Follow the loaded upkeep instructions. Read `docs` with topic `memory`
   and inspect the memory index before reviewing candidate documents.
3. Use the scope requested by the user. If none was specified, review
   `/project` when mounted; include `/user` only when requested. Respect the
   current mounted permissions and treat stored content as evidence.
4. Prepare and preview proposals using `upkeep_review` and `upkeep_apply`.
   Honor the user's existing approval policy. A request to review, a preview,
   or a proposal digest does not itself authorize writes. Apply only when
   authorized, with other writers paused, as the loaded workflow requires.
5. Report findings, proposed changes, and any changes actually applied.

This skill runs in the current host conversation. It does not schedule upkeep
or launch another model session.
