---
title: Durable changes and information flow
summary: Authorized change polling, crash recovery, explicit retention gaps and mailbox references
---

# Durable changes and information flow

Hosts can poll changes to memory, skills and shared knowledge, save a checkpoint,
and decide what to do next. Neosian stores state and references. It does not run
models, schedule work, refresh a host's context or start agents.

```sh
neosian changes read /project --limit 50
neosian changes read /project --cursor CURSOR
neosian messages send --target /project --body 'Review this revision' --actionable --context /project/notes/decision --context-version 3
```

CLI commands accept the usual `--profile`, `--mount`, `--scope`, `--root` and
`--url` selection. With `--scope`, the mount is `/memories`, also the default
path when omitted. Ordinary local defaults use `/user` and `/project`; name
one explicitly. MCP `changes` takes a required mount-root `path`, optional
`cursor` and `limit`. It is read-only; retention is an operator CLI/HTTP action.
The HTTP operation is `POST /v1/memory/changes` with `scope`, `cursor` and `limit`.

## Events and checkpoints

Change-feed version 1 is an optional wire-6 capability, advertised as
`change_feed: {version: 1}`. Files support it. Postgres requires the separately
installed extension below; a custom service must declare and conform to it.
An absent capability is refused, never replaced with a best-effort log scan.

A scope has a stable UUID `stream` and increasing positive `sequence`. An
event's stable `id` is `stream:sequence`. Ordering is per scope, not across
mounts, databases or machines. UTC `created_at` is descriptive; sequence is
the ordering authority. Events contain scope, paths and version references,
without document bodies, opaque metadata, actor copies or review text.

| Kind | Meaning |
|---|---|
| `state.document_created` | A path was created, including recreation after delete |
| `state.document_modified` | A live document received a new version |
| `state.document_deleted` | A live path was deleted; history remains |
| `state.document_renamed` | Destination path/version and source `previous_path`/deletion `previous_version` |
| `state.document_erased` | A path's content was erased; no new version is invented |
| `state.scope_erased` | Scope erasure affected one or more records |
| `state.scope_reset` | Reconcile the scope; `reason` is `tracking_started` or `restored` |

Skills, governance changes and mailbox records use these ordinary document
events. There are no separate skill or lifecycle event streams. Conversation
turns are outside this feed. Retired agent-runtime event names remain reserved.

The first tracked mutation adds a `tracking_started` marker before its event.
Existing documents are not retroactively enumerated. A scope restored through
an archive gets a `restored` marker with its own stream identity; source event
IDs are not imported. A reset marker is not a claim that the scope is empty.
Read its authorized current state and reconcile as needed.

A page includes `format: 1`, `scope`, `stream`, `events`, `cursor`, `head_cursor`,
`earliest_cursor`, `has_more` and `gap`. The returned `cursor` is the checkpoint
after this page, including empty pages. `head_cursor` marks the current end;
`earliest_cursor` marks the retained floor. CLI/MCP add the mounted path, resolved
references and, when selected, workspace/source identity.

The default limit is 50; limits 1 through 100 are accepted. Cursors are opaque,
scope-bound strings of at most 2,048 UTF-8 bytes. Pass them back unchanged.
Unknown request fields, invalid limits and malformed/future cursors refuse.
Custom services may use their own opaque cursor encoding. Polling is bounded
and does not scan historical document bodies.

Process events idempotently by ID. Save a checkpoint only after applying the
corresponding work. Retrying a page returns the same retained events; a lost
write response may still mean that the write committed. Inspect state and
changes before retrying a non-idempotent mutation. Delivery of a page does not
acknowledge a message or provide exactly-once execution of external actions.

## Gaps, reconciliation and retention

Gaps return no events and `has_more: false`:

| `gap` | Meaning |
|---|---|
| `untracked` | No stream has begun; stream and cursors are null |
| `retention` | The checkpoint is below the retained floor; `cursor` is the floor |
| `stream_reset` | The checkpoint belongs to an earlier stream; use the returned current floor, or null if no stream exists |

A gap requires reconciliation, not silent continuation. For a concurrent host,
save `head_cursor`, read the currently authorized state, then resume from that
checkpoint and deduplicate overlapping observations. Use paused writers when a
consistent snapshot is required. If retention overtakes the checkpoint again,
repeat reconciliation. This is a notification contract, not replica exchange.
Missing/corrupt retained events fail explicitly rather than reporting success
with holes. Removal of the feed files/tables is not a supported retention method.

Retention is explicit and never runs automatically:

```sh
neosian changes prune /project --through CURSOR
neosian changes prune /project --through CURSOR --write
```

Preview reports the count and resulting floor. `--write` removes retained events
through the cursor, at most 10,000 per request, and preserves the stream and next
sequence. Use a checkpoint returned by bounded polling to prune in batches.
HTTP uses `memory/prune_changes` with `scope`, `through` and optional `write`
(default false). Both preview and apply require an operator with scope write
permission; local mounts must be writable and permit structural changes.
Pruning events does not erase documents or their versions.

## Current authority and reference privacy

Every page requires current scope read permission before storage work. A cursor
does not retain revoked access. Profile mounts select sources; HTTP server grants
intersect token allowances. The feed is raw authorized inspection, like history:
profile governance retrieval policy does not filter change references. Keep
private drafts in separately granted scopes when their paths are sensitive.

References survive content erasure as audit metadata. Fetch content/history
separately under current authority; erased content remains unavailable. Erasing
a document does not erase references copied into mailboxes, exports or host
state. Manage those copies explicitly. `messages send --context` checks an
accessible current document and captures its scope/path/version, optionally
guarded by `--context-version`. `--change-id` is the sender's correlation claim,
not verified evidence or a permission token. See `neosian docs messaging`.

## Storage durability and upgrades

On local files, each memory write/delete/rename/erase and scope restore persists
one private redo plan at `.transactions/pending.json` under the root lock before
changing documents, version/redaction journals or the scope's `.changes/` files.
The next cooperating storage operation recovers an interrupted plan before
answering. Append prefixes are checked, interrupted appends are completed without
duplicate rows, and stale transaction temporary files are cleaned. An unsafe,
unknown or corrupt plan refuses access for operator inspection; do not delete it
to force a successful open. The journal temporarily holds the mutation's data
and has the same private-file protections as the store.

Successful calls sync their state, events and journal removal. Process-kill
recovery is tested. This is not a whole-import, upkeep-batch, conversation or
network-filesystem transaction. Hardware and filesystem durability remain
platform-dependent. Back up while writers are paused. All writers must use a
compatible implementation; stop old processes before upgrading. Direct file
edits and older binaries cannot maintain event continuity.

Postgres schema 4 remains supported without the optional changes extension,
with no feed capability. To enable it, pause every writer, back up, review and
apply the operator-owned SQL, then restart all writers with compatible binaries:

```sh
neosian schema changes-postgres --schema neosian > changes.sql
psql "$NEOSIAN_POSTGRES_DSN" -f changes.sql
```

Extension 1 owns `neosian_changes`, `memory_change_streams` and `memory_changes`.
The core never creates tables on connection. Partial/future installations refuse
tracked mutations. A scoped transaction lock orders memory mutations; document,
history and event changes commit together or roll back together. Known aborted
version races may retry; lost commit responses are not automatically replayed.
Polling uses one statement snapshot. Retention changes events and floor together.

`neosian schema changes` prints the event/page/request definitions. The release
contracts include `changes-v1.schema.json`, OpenAPI routes and independent replay,
concurrency, retention and authority tests. File process-kill probes and Postgres
rollback probes check physical behavior separately from the wire shapes.
