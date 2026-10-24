---
title: "Skills: documents in a mount, versioned, served"
summary: a skill is skills/<name> in a mount - versioned, curated by flag, served as MCP prompts
---

# Skills

Profile mounts can require `knowledge_policy = "effective"` or `"approved"`.
Selected skill roots, dependencies and supporting documents must qualify at load
time; excluded graphs refuse. Raw authorized reads and management inspection
remain available. Built-in immutable skills are separate assets. See
`neosian docs governance` for review assertions and authority boundaries.

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
- `eo`: a fixed set the agent may revise but not
  extend: pre-created skills, no new ones, no deletes.
- `ro`: immutable reference skills; a second agent
  mounts the same scope read-only and can only load.

## Load one

Portable profiles explicitly select `/mount/skills/name` and `builtin:name`
entries. Only those entries appear in skill discovery, loading and MCP
prompts; bare names do not select a profile skill. Missing selections are
reported. Lists carry workspace/source/scope provenance and the current
document version. This is context selection, not secrecy from a permitted
memory reader. Imported profile mounts default to `ro`; the `rw` default
above describes legacy mounts. See `neosian docs profiles`.

The MCP server exposes two read-only tools beside `memory`:

- `list_skills()`: every skill with its description; a skill in a
  mount also shows its path and version. When a writable mount exists
  the reminder carries the writing guide above.
- `load_skill(name)`: the instructions, by name or by
  `/mount/skills/name`; the reminder names the document and version,
  so a revision is one `str_replace` away.

Both read the store live: a skill written this session is loadable
this session. The memory index lists skills under `skills/` like any
document, so the frozen index shows what exists and the tools show
what it is for. Shipped skills are immutable assets beside the mounts'.
`search_memory` can find text inside permitted stored skill documents even
when their names do not reveal the relevant subject. It returns the current
document reference and version; skill selection and load permissions still
follow the profile rules above. Shipped immutable skill assets are not stored
memory and remain discoverable through `list_skills` and MCP prompts.

## Served to any agent

The MCP server (`neosian docs mcp`) serves `list_skills` and
`load_skill` beside `memory`, on stdio and at the state process's
`/mcp`, so Claude Code, Codex, Cursor and other MCP clients read the same
skills from the same store. Every skill is also an **MCP prompt**
(`prompts/list`, `prompts/get`): clients that render prompts as
commands (Claude Code shows `/mcp__neosian-memory__release`) get a
skill written by one agent as a command in the next. The binary ships `handoff` and `upkeep`, served as prompts after
the mounts' skills and never seeded into a store: it tells the agent to
write the note the next session reads with the `handoff` tool; `upkeep` guides grounded memory consolidation
(`/mcp__neosian-memory__handoff`); `list_skills` on the server lists it
bare, with no path or version, like a directory skill.

Serving an owner's skill scope over the network, versions and all, is
the state process on that scope (`neosian serve`); a host operating it
for many owners is a host's product, outside the core.

Stored skill documents can be brought in as Markdown through `knowledge
import` with a deliberate collection prefix. This preserves readable content
and provenance; it does not add skill execution or
change a profile's skill selection. Existing discovery and load rules apply.

## Manage a skill

`neosian skill` provides a reviewable workflow using the same storage and mount
flags as memory. Reports are JSON, with or without `--json`. Mutations preview
by default; `--write` applies. Pause other writers during import, edit, revert
and export, and keep input/output files stable. The existing storage wire has
no batch transaction or atomic absent-path create. Partial imports report their
landed paths/versions in `applied`; they do not roll them back.

```sh
neosian skill import ./review --to /memories/skills/review --scope user:demo
neosian skill import ./review --to /memories/skills/review --scope user:demo --write
neosian skill discover --scope user:demo
neosian skill inspect /memories/skills/review --scope user:demo
neosian skill validate /memories/skills/review --scope user:demo
neosian skill load /memories/skills/review --scope user:demo
neosian skill edit /memories/skills/review --file ./revised.md --expect-version 1 --scope user:demo
neosian skill diff /memories/skills/review --from 1 --scope user:demo
neosian skill revert /memories/skills/review --version 1 --expect-version 2 --scope user:demo
neosian skill export /memories/skills/review --output ./review-copy --scope user:demo
```

Import accepts a Markdown `.md` file or a directory containing `SKILL.md`.
The destination must be `/mount/skills/name`; its name must agree with an
optional frontmatter `name`. Existing different content refuses unless
`--replace` is explicit. Identical generated content/provenance is a duplicate
and consumes no version. Erased destinations and absent paths with retained
history refuse, including under `--replace`. Imports never remove old files
that a new package no longer mentions.

Edit replaces the root instructions. An unmarked input preserves existing
knowledge metadata, dependencies and file hashes; an input carrying a knowledge
header explicitly supplies them. Edit adds source evidence and preserves normal
destination envelope extras and creation time. `--expect-version` must match the
current root. Revert copies a retained, non-erased root revision into a new
version after checking its current dependencies and supporting files. It cannot
restore a deleted root or erased data, and does not rewind supporting files.
Use `memory versions` for the audit rows. Diff/revert look within the most recent
1000 rows; diff returns one exact replacement hunk (`removed`, `added`, and
`common_prefix_lines`), including any metadata changes. It is not a minimal diff.

Inspect and validate can address any permitted stored skill, including one not
selected by a profile. Discover, load and MCP prompts honor profile selection
and revision guards. A malformed skill or an unavailable dependency is reported;
its instructions are not returned by the loader. Ordinary authorized memory
reads remain possible. Imported knowledge headers are understood before the
skill's frontmatter is parsed, so skills imported through `knowledge import`
remain discoverable.

## Portable supporting files and dependencies

A directory package can contain text references, examples or scripts alongside
`SKILL.md`. Without a manifest or embedded skill specification, all regular
files become supporting text. Binary
files, NUL bytes, symlinks, traversal and unsupported logical filenames refuse.
Filenames retain their extensions. No file is installed into an agent directory,
executed, or given executable permissions by this workflow.

An optional `skill.json` explicitly declares format 1:

```json
{
  "format": 1,
  "requires": [{"skill": "evidence"}, {"skill": "builtin:upkeep"}],
  "files": {
    "references/reason.md": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "collections": ["decisions", "procedures"]
}
```

Replace the example digest with the SHA-256 of that file's UTF-8 body bytes.
The file list must exactly match the supporting files present. Hashes are of
the body after a recognized knowledge header, preserving all body line endings.
Unknown fields, versions and duplicate JSON keys refuse. `neosian schema skill`
prints the structural contract. A supplied manifest replaces the root's existing
skill specification deliberately; otherwise an existing specification is retained.

The specification is stored as `attributes.skill` in the root's format-1
knowledge header. Supporting files are ordinary versioned documents at
`skills/name/<relative-file>`, each with its own knowledge header/provenance.
The root records their hashes; loading checks them through mounted reads.
Dependencies and supporting files are listed with source/revision references,
without automatically loading their instructions or executing their contents.
An agent can load a dependency explicitly and read supporting text with memory.
Raw edits can invalidate a file hash; update the package/root specification after
review. Resources are written before the root, so a partial replacement can make
an older root fail validation rather than silently use changed resources.

A bare dependency name resolves in the skill's own mount. A qualified
`/mount/skills/name` selects that mounted source; `builtin:name` selects a shipped
skill. Dependencies must be available, valid and readable. In a profile they
must also be selected, and their profile pins apply. Cycles refuse. An optional
`revision` object containing positive `version` and lowercase `sha256` guards a
stored dependency's current root, just like a profile pin. Built-ins have the
binary's version and cannot use a stored-revision guard. Nothing installs or
fetches an unmounted dependency.

Collection entries are descriptive paths relative to the skill's mount. They
associate instructions with knowledge collections without creating those
collections or granting access. Existing explicit knowledge references and other
attributes remain intact. Changing a mount changes these relative associations;
inspect the resulting paths when importing into another workspace.

Export creates a new private directory and writes `skill.json` last as its
completion marker. An interrupted export can leave an incomplete directory;
use a new output path to retry. It preserves current text, structure and source
envelopes as provenance, rather than restoring source identity at import.
Destination versions, actors and timestamps remain normal new writes. Use full
storage archives for faithful histories and envelopes. Root erasure does not
cascade to separate supporting documents or earlier exports; erase those
separately. A package cannot infer later source erasure at a fresh destination.

The limits are 64 supporting files per skill, 16 dependencies per skill, 32
collection associations, dependency depth 8 and 64 stored-skill visits, 256
supporting-file visits per resolution, 1 MiB per stored document and 20 MiB per
package/resolution. Knowledge header limits also apply. Directory traversal is
bounded to 4096 entries and 16 levels; discovery permits at most 10000 listed
entries per mount after the backend listing. These limits do not bound backend
listing work. Files cannot alias directories, control files or differently cased
path components. Path checks are not isolation from hostile filesystem writers.

## Profile selection and revision guards

Profiles keep qualified references and optional guards, never copied skill
bodies. Selection changes below require an explicit `--profile`. Select,
unselect and unpin edit configuration without opening a data store; pin needs
an available current skill. Changes preview unless `--write` is given.

```sh
neosian skill select /project/skills/review --profile portable --write
neosian skill pin /project/skills/review --profile portable
neosian skill pin /project/skills/review --profile portable --write
neosian skill unpin /project/skills/review --profile portable --write
neosian skill unselect /project/skills/review --profile portable --write
```

A pin records the logical source, scope, current version and full content hash
in the portable profile's optional `skill_pins` table. It guards the current
revision: a changed, missing, deleted or erased root refuses. It does not load
historical content or fall back to another same-named skill. Supporting hashes
are still checked even when the root version is unchanged. Review with inspect
and diff, then run pin again to accept the current revision, or unpin to follow
live revisions. Unselect also removes that selection's pin. Built-ins can be
selected explicitly but do not have stored revision pins.

Pinning does not change mount or server permissions, authenticate supplied
provenance, or restrict ordinary authorized memory reads. A logical source can
be rebound on another machine; a pin is not a physical database identity.
Profile exports include the guards, without endpoints, credentials or skill
bodies. Format-1 profiles without pins remain unchanged; older strict binaries
refuse the new field, so use a compatible binary on every participating machine.

Profiles use qualified names to disambiguate sources. Legacy configurations
retain their existing bare-name precedence: mounts in order, then shipped
skills. Explicit paths or `builtin:name` avoid ambiguity. MCP tools and prompts
include source/version details for managed or profile-selected skills.

The shipped `upkeep` skill uses `upkeep_review` and `upkeep_apply` for grounded,
reviewable memory changes under the user's or host's approval policy. It does
not execute a model or schedule work. Upkeep may edit stored skill documents
as ordinary knowledge, but does not validate their dependency/resource graph
or update profile pins. Run `skill validate` and deliberately review any
changed pin before loading. See `neosian docs memory` for the full workflow.
Representative raw MCP clients and all six setup registrations are tested;
a client's command rendering and context limits remain that client's behavior.
The tests do not claim identical model execution or live-client UI behavior.
