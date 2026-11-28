---
title: "Portable workspaces and context profiles"
summary: Stable workspace identity, local source bindings, server grants and explicit capture policy
---

# Portable workspaces and context profiles

A profile selects the same knowledge and skills across machines and agents.
Its workspace UUID and scope names are independent of your OS username,
checkout directory and agent account. The portable TOML file describes the
selection; each machine supplies its own endpoints and credential references.
An imported profile grants no server permission. **New profiles capture no
conversation data until you opt in.** Existing profile-free hooks keep their
existing recording behavior.

## Create and bind

```sh
neosian profile init work                  # JSON preview, no files created
neosian profile init work --write
neosian profile bind work --source workspace --root /absolute/path/to/knowledge --project "$PWD"
neosian profile bind work --source workspace --root /absolute/path/to/knowledge --project "$PWD" --write
neosian profile show work
neosian setup --profile work --client codex # preview the selected policy
neosian setup --profile work --client codex --write
neosian setup --profile work --client codex --check
neosian memory view / --profile work
neosian status --profile work --json
```

`init` creates a UUID, a `workspace:<uuid>` scope at `/project`, a read-write
mount and the explicit `builtin:upkeep` skill. Capture is off. An omitted
mount `access` in an imported file defaults to `ro`. `bind --project DIR`
associates a local checkout with the profile; it does not write project files.
Bindings can name existing scopes without moving or rewriting their data.

Selection order is `--profile NAME`, then `NEOSIAN_PROFILE`, then the longest
registered project root containing the current directory, then a directory a
login import resolved (the exact directory login bound, else the workspace
the directory's historical scope `user:<login>/proj:<name>` was imported to,
while it stays assigned; `neosian docs login`), then the default from machine
login. Recorders use their
client's project anchor (`--project`, or Cursor's workspace roots). Selection
does not merge profiles. An absent, invalid or unbound selected profile fails
closed. Legacy `--root`, `--url`, `--schema`, `--scope`, `--mount` and ambient
`NEOSIAN_SCOPE` conflict with a profile. An ambient `NEOSIAN_POSTGRES_DSN`
also conflicts unless a selected Postgres binding explicitly references it.

Profiles live at `$NEOSIAN_HOME/profiles/<name>.toml`, with private local
bindings and an installation UUID in `$NEOSIAN_HOME/profile-bindings.toml`.
Without `NEOSIAN_HOME`, the home is `~/.neosian`. Config files and their
immediate profile directory may not be symlinks. A local profile alias is
`[a-z][a-z0-9_-]{0,63}`. Replacing a profile, source binding or project binding
requires `--replace`. Changing selection requires restarting the MCP client;
hook invocations resolve the current configuration on every event.

## Exchange a selection

An optional mount `knowledge_policy = "effective"` or `"approved"` sets the
minimum review policy for memory search, knowledge reads/references, injected
memory and selected skill graphs. The omitted default is `all`. Requests can
tighten the policy; raw memory/history reads and exports remain authorized
inspection surfaces. Built-in skills and transcript/mailbox selection keep their
separate rules. Read `neosian docs governance` before enabling the filter.
Profile export retains this choice; older strict readers refuse the added field.

```sh
neosian profile export work > work.toml
neosian profile import work work.toml       # validate and preview
neosian profile import work work.toml --write
neosian profile bind work --source workspace --url https://state.example --token-env WORK_MEMORY_TOKEN --write
```

Export contains only the portable schema: no endpoint, local path, credential
reference/value, installation UUID, spool or stored document. Scope names,
selected document paths and configured literal filter patterns are ordinary
profile data and are exported; keep secret values out of those fields.
Import never selects a new profile, opens a store, installs a client or grants
capture permission. Replacing an already selected alias takes effect on the
next invocation; its existing local capture grants still apply. All management commands return JSON except `export`, which
prints TOML. The JSON Schema for parsed TOML is `neosian schema profile`.
Unknown keys and unsupported formats are refused; files are limited to 256 KiB.

An HTTP binding requires `--url URL --token-env VARIABLE`. URLs exclude
userinfo, query and fragment. A Postgres binding uses `--dsn-env VARIABLE`
and optional `--schema NAME`; it opens an already provisioned schema and
never applies DDL. A file binding uses `--root DIR`. All sources must be bound.
Manually bound credentials are read from the client's environment at use, never copied into
exports, setup output or registrations. Muse/Cursor forward the variable
names; a custom `NEOSIAN_HOME` is forwarded too. `profile show` and `status`
report missing variable names and configuration readiness without connecting;
authorization is checked by the backend when used.

## Select memory, skills and context

```toml
profile_format = 1
workspace_id = "11111111-2222-4333-8444-555555555555"
name = "Shared project"
sources = ["team"]
skills = ["/project/skills/review", "builtin:upkeep"]

[[mounts]]
source = "team"
scope = "workspace:shared-project"
path = "project"
access = "ro"
injection = "full"
documents = ["decisions/current"]

[context]
max_chars = 8192
mailbox = false

[capture]
mode = "off"
```

Each of 1–32 mounts selects one declared source, exact scope and unique
single-segment virtual path. A real scope has only one mount in a profile;
it cannot route to two sources. `rw` permits writes, `ro` only reads, and
`eo` edits an existing document set. These restrict the selected view; they
cannot expand backend authority. Cross-source rename is refused. A
cross-scope move within one source keeps its existing non-atomic behavior.

`index` injects references, `full` injects only the named documents, and
`none` injects no memory content automatically. These modes do not remove
tool read access. Full selection excludes session, handoff and mailbox
records. History has its own selection below, and automatic mailbox context
requires `context.mailbox = true`. Skills are explicitly qualified paths or
`builtin:handoff` / `builtin:upkeep`; no bare-name shadowing occurs. A selected
missing or malformed skill is reported. Tools and MCP prompts read the
current version on each call. A permitted memory reader can still view an
unselected skill document explicitly.

Context has one character budget, default 8192, configurable from 512 to
65536 and further bounded by the client. Framing, identity, source references
and the companion MCP instructions count. Whole items that do not fit are
omitted with a count; oversized documents are never silently injected in
part. Mounts, explicit documents and skills retain profile order. Source,
scope/path and version identify selected content. This bounds neosian's
injection, not the host's context window or tool responses. Listing and
reading a source can still require backend work beyond that output budget.

`memory search` and MCP `search_memory` search current mounted documents
independently of injection mode or capture. Results add the workspace UUID,
logical source and mounted reference to the real scope/path/version. The
search cursor is bound to the mounted selection and preserves backend cursor
opacity. An HTTP source must additionally advertise memory-search version 1;
unsupported sources refuse instead of falling back to a document download.
Relevance and newest orders and date bounds also need memory ranking version 1
at every selected source; otherwise an omitted order uses path order and says
so. Each mount is ranked from its own documents and the lists interleave.
See `neosian docs memory` for fields, metadata selection and pagination.

## History and capture

No `history` means no raw-history tools or startup transcript summaries.
To read old records without capturing new ones, select one history source,
its sessions-document scope, and either a literal `prefix` or a nonempty
`conversations = ["existing-id", ...]` list. Capture may stay off.
The profile checks the IDs before recall, projection, search or continuation;
a forged session listing cannot widen this boundary. Prefix searches discover
only matching, nonredacted `sessions/<id>` entries. Unlisted logs are not
discovered; name an authorized conversation explicitly to read one. A listing
over 10,000 entries is refused; narrow to explicit IDs (at most 256).
An empty candidate list never becomes a whole-store search.
Ranked or time-bounded history search runs at the history source and needs
history ranking version 1 there; otherwise an omitted order uses newest first
and says so.
Turns kept as records follow the same boundary. A record write goes only to
the history source, with capture on and a conversation the selection admits
for writing, exactly as a whole-turn append; it never reaches a memory
source. A record read needs an admitted conversation. A record search covers
only the admitted conversations, also when the caller names none, so an
HTTP history source is always sent the list it may search; an empty
selection answers no hits without asking the source. Stale-draft sweeps and
record restores are operator commands on a named store and never run
through a profile (`neosian docs stores`).
With capture off, continuation reads without writing pickup/continuation
receipts. Separate history scopes do not write continuation receipts into
memory mailboxes; excluded tool payloads do not contribute tool-derived
lineage to newly recorded turns. Existing lineage remains readable.

To capture locally, add a separate `history` source to `sources`, then:

```toml
[history]
source = "history"
scope = "workspace:private-history"
prefix = "w-11111111222243338444555555555555-"

[capture]
mode = "local"
fields = ["prompt", "assistant"]
exclude_tools = ["secret-tool"]
remove_keys = ["password", "authorization"]
replacements = [{from = "synthetic-marker", to = "[removed]"}]
```

```sh
neosian profile bind work --source history --root /absolute/private/history --capture local --write
```

Capture requires both the profile and the local binding to permit the same
mode. `local` requires a FileStore root separate from and non-overlapping
every memory file root; it cannot be a mounted memory source. Raw turns,
session summaries and projections stay there, even with remote knowledge.
`shared` deliberately sends them to the named history source, which can
use HTTP, Postgres or files, and requires `bind ... --capture shared`.
New recording requires the exact `w-<workspace UUID without hyphens>-` prefix.
Explicit legacy ID lists are read-only. A mounted capture scope must be `rw`.

The default captured fields are prompt and assistant text. Optional
`tool_inputs`, `tool_outputs` and `received_context` enable those fields.
Exact, case-sensitive tool exclusions, recursive removal of named structured
object keys, and ordered literal replacements apply before reducing the hook
payload, persisting the spool, sending a turn or generating its summary.
Strings containing serialized JSON remain strings; key removal does not parse
them. Replacements scrub captured text, tool names and call IDs. Source
identity and lifecycle markers remain provenance. This is deterministic
filtering, not universal secret detection. No transcript files are read.
Capture policy neither filters explicit memory/tool writes nor changes the
agent host's own logs, and it does not erase existing records.

Conversation IDs combine the workspace prefix with a SHA-256 mapping of
installation UUID, client kind and native session ID (99 characters total).
The same installation/client/session maps consistently; separate machines
and clients do not collide by merely reusing a native ID. The original actor
and native session ID remain in recorded provenance. Copy the portable
profile between machines, not the machine's installation bindings.

To pause recording of one profile conversation on this installation, copy its
complete ID from the `[neosian session identity: ...]` line. Clients that omit
this line can use the `session_id` in their `record --json` diagnostic envelope.
A SessionStart diagnostic reads context and leaves the recording untouched. Use
the same client, native session and workspace when obtaining the ID. Pi and
OpenCode expose the mapped ID in their normal prompt context. Run:

```sh
neosian record pause <conversation-id>
neosian record resume <conversation-id>
```

Pause waits for any hook already landing a turn. Once it returns, the
recorder has discarded this conversation's open turn and its closed turns
waiting to land, and suppresses new capture events. A lander sending one of
those turns stops before its next part, discards the open draft and the turn,
and lands nothing more of the conversation. A part already in flight when the
pause is given may still be applied, and a seal in flight finishes its turn;
the pause does not wait for a lander. Turns held aside stay. Previously recorded turns remain in history. Startup context,
mailbox delivery, knowledge and skills stay available. Resume follows the
workspace capture policy from the next user prompt. Until that prompt,
delayed tool results and assistant replies from the interrupted turn are
discarded. If the client changes workspace mid-turn, the newly selected
workspace must have a pending turn or receive its own fresh prompt.

Cursor and the OpenCode plugin identify the native generation of a reply.
After a pause the recorder accepts their callbacks only for the admitted
prompt's generation. Older or unidentifiable callbacks are discarded, even
after another prompt. `neosian update`, or `neosian setup --refresh` after a
package-manager upgrade or a new major installed by hand, replaces an older
installed OpenCode plugin and keeps
its registration options. Other clients resume at their next observed user
prompt. Both commands run locally and work offline. Private control files
under `profile-spool/` hold empty markers and digests of native generation
IDs. They contain no prompt text, assistant text or credentials.

Pending filtered spans live under the private `profile-spool/` directory in
the local home. For manual profiles, a companion `.policy` fingerprint binds
the open span to the entire profile, local source bindings and hashes of
referenced credential values; secret values themselves are never persisted.
At the stop the span is closed into `pending/<id>/<n>.jsonl` with that
fingerprint in its `<n>.state`, and a closed turn lands only while the current
fingerprint is the one it was closed with. Otherwise it is held under
`held/<id>/` with reason `policy_changed`, and later turns still land. Nothing
recorded under one policy is sent under another.

A changed policy, binding or DSN/token value (even when its variable name is
unchanged) no longer stops recording. The first recorded event under the new
fingerprint closes the open span, holds it with `policy_changed`, and starts a
new span with that event, so a turn can begin in the middle of an exchange. A
held turn is never sent by itself. Disabling capture keeps closed turns queued
and unsent (the hook answers `capture_off_pending`); they land once capture is
on again, by the lander the next hook event starts. `record pause` discards the open span and the
conversation's closed turns waiting to land; held turns stay.
Never transfer a closed turn to a different destination by editing its state.
A closed turn for a profile with any source that is not a local root is landed
by a detached lander, never inside the stop (`neosian docs agents`). A failed
landing keeps the closed turns queued for the next lander. A crash
between backend writes can leave partial results or duplicate a retry; this is
not exactly-once or a cross-store transaction.

Host-managed profiles keep the guard bound to the verified host/account,
workspace, history destination, consent and capture filters. An unrelated
skill update or same-account credential rotation can finish an open span.
A new environment credential requires a live login first. See `neosian docs login`
for automatic checkout selection and credential storage.

## Server authority and client limits

Profile HTTP sources require the opt-in `authorization` capability described
in `neosian docs wire`; older/unscoped backends are refused. The operator maps
authenticated token actors to exact memory-scope read/write grants and
conversation-prefix or explicit-ID read/append grants. No caller-supplied
profile, actor or agent account grants access. OS permissions and database
credentials remain the authority for file/direct Postgres access. Profile
restrictions cannot confine a process that owns those broader credentials.

Use the local stdio MCP bridge for constrained remote access. Direct hosted
`/mcp` is unavailable when server grants are enabled. Setup/status expose
`profile_support`: Claude Code, Codex, Muse Code and interactive Cursor use
startup hooks; OpenCode uses MCP instructions; Pi uses its prompt section.
Cursor print mode lacks full capture hooks, and Claude Desktop has no capture
hooks. Preview does not prove that a client loaded or trusted its hooks; the
existing client trust/setup rules still apply (`neosian docs agents`).

Profile-aware commands are knowledge, memory, messages, search, continue, MCP, record,
setup and status. Whole-store operators (`audit`, `export`, `import`, `redact`,
`prune`, `serve`) require an explicit legacy store outside any active profile.
Run them from an unbound directory with `NEOSIAN_PROFILE` unset; a portable
profile export is distinct from a data archive. Use trusted operator authority
for backups and erasure. Profile-free invocations retain their existing
selection and data formats; profiles never silently migrate stored history.

## Managed skill revisions

`neosian skill select/unselect` previews qualified selection changes; `pin/unpin`
previews current-revision guards. Each requires an explicit `--profile`; `--write`
applies. Optional `skill_pins` records source, scope, version and content SHA-256
for a selected stored skill. A changed, missing or erased skill refuses until
explicitly reviewed and repinned/unpinned. No historical body or configuration
copy is substituted. Supporting files and selected dependencies are checked too.
Export carries these references/guards without credentials or endpoints. Older
strict binaries refuse profiles containing the new field. See `neosian docs skills`.

Reviewed upkeep uses the selected workspace and mounted logical sources.
Proposals bind that selection and recheck current mount and server authority;
capture and history consent do not gate ordinary document upkeep. The shipped
`builtin:upkeep` skill guides scan, prepare, preview and explicitly authorized
apply. A proposal digest is not an approval identity or a grant. No maintenance
policy is imported from document text. Read `neosian docs memory` for limits.

`changes read /project --profile NAME` polls one explicitly selected memory
mount and reports source/workspace identity. It cannot use a history-only scope.
Each HTTP page rechecks server grants; a saved cursor grants no access. Change
references are raw audit inspection, independent of context governance filters.
Separate scopes protect private path metadata. See `neosian docs changes`.

## Explicit memory exchange

`exchange snapshot` and `exchange delta` can select one profile mount with
current source read authority, including a read-only mount. Governance selection
policies do not filter these raw inspection exports. They carry ordinary current
memory and skills, excluding raw turns, lifecycle state and binding credentials;
capture/sharing settings are not widened. Receivers use an explicit local root
and matching scope, outside profile routing. See `neosian docs exchange`.
