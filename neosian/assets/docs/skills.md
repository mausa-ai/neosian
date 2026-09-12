---
title: "Skills: documents in a mount, versioned, served"
summary: a skill is skills/<name> in a mount - versioned, curated by flag, served as MCP prompts
---

# Skills

A skill is reusable instructions (how to release, how to review, how
to file a ticket) that an agent loads on demand instead of carrying in
every prompt. In neosian a skill is a **document in a memory mount**:
the document `skills/<name>` under any mount, so `/user/skills/deploy`
is yours, `/project/skills/deploy` is this project's, and a read-only
mount holds a team's. Nothing about it is new machinery: the mount's
scope owns it, the mount's flag says who may write it, and the version
rows, receipts, redaction, `revert`, and `audit` are the store's.

## Write one

Through the memory tool, from any transport (an agent's `memory`
tool, `neosian memory`, MCP, the state process):

```bash
neosian memory create /project/skills/release --content - <<'EOF'
---
description: Release this project
---
1. Bump the version.
2. Run make test.
3. Tag the release.
EOF
neosian memory versions /project/skills/release   # the history
```

The contract: **the address is the name** (`release` is the path's
last segment) and the frontmatter needs one key, `description`, the
sentence that tells the model when to use it. A `name` key is
optional and must match the address. `version` and `owner` never
appear in frontmatter: the version row and the scope are the truth.
Deeper paths (`skills/release/checklist`) are a skill's supporting
documents, readable with `view`, never listed as skills.

Revise with `str_replace` on the path, and every edit is a version row
with its actor (`conv:<id>#<turn>` for an agent's own write). A
document that breaks the contract is listed with the error its author
must fix, so the agent that wrote it sees it at once.

## Curate by mount flag

Mutability is the mount's, exactly as for memory:

- `rw` (the default): the agent writes and revises skills.
- `eo` (`edit_only=True`): a fixed set the agent may revise but not
  extend: pre-created skills, no new ones, no deletes.
- `ro` (`read_only=True`): immutable reference skills; a second agent
  mounts the same scope read-only and can only load.

## Load one

Every memory-configured agent (an `Agent` with `memory=`, a
`Conversation` on mounts) carries two read-only tools beside `memory`:

- `list_skills()`: every skill with its description; a skill in a
  mount also shows its path and version. When a writable mount exists
  the reminder carries the writing guide above.
- `load_skill(name)`: the instructions, by name or by
  `/mount/skills/name`; the reminder names the document and version,
  so a revision is one `str_replace` away.

Both read the store live: a skill written this session is loadable
this session. The memory index lists skills under `skills/` like any
document, so the frozen index shows what exists and the tools show
what it is for. `AgentConfig(skill_dir=...)` stays the directory
source (`*.md` files whose stems are the names) merged in as
immutable skills beside the mounts'.

## Served to any agent

The MCP server (`neosian docs mcp`) serves `list_skills` and
`load_skill` beside `memory`, on stdio and at the state process's
`/mcp`, so Claude Code, Codex, Cursor and a neosian agent read the same
skills from the same store. Every skill is also an **MCP prompt**
(`prompts/list`, `prompts/get`): clients that render prompts as
commands (Claude Code shows `/mcp__neosian-memory__release`) get a
skill written by one agent as a command in the next.

Serving an owner's skill scope over the network, versions and all, is
the state process on that scope (`neosian serve`); a host operating it
for many owners is a host's product, never library scope.
