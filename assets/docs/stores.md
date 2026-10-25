---
title: "Storage and conformance"
summary: Format-1 files and records, schema-4 Postgres, custom HTTP backends and independent conformance
---

# Storage and conformance

Portable profiles route explicitly selected mounts to file, Postgres and HTTP
sources without changing stored formats. HTTP profile bindings require the
separately versioned scoped-authorization capability; ordinary storage
conformance does not certify it. Direct files and database credentials remain
operator authority. See `neosian docs profiles` and `neosian docs wire`.

The supported backends are FileStore and Postgres. Custom backends run as
independent HTTP services implementing wire 6. There is no public embedding
SDK or storage subclass API. The contract consists of the schemas, documented
behavior, literal fixtures and independent runner, not an implementation class.

## FileStore layout

A scope `user:me/proj:demo` maps to `user%3Ame/proj%3Ademo/` beneath the root.
`documents/notes/fact.md` contains a format-1 YAML envelope between whole-line
`---` fences, then the exact body. Required envelope fields are
`neosian_format: 1`, positive `version`, `created_at` and `updated_at` with
explicit timezones. Optional `actor` and `redacted` retain their types.
`versions/notes/fact.jsonl` holds full-content mutation rows; `redactions.jsonl`
holds the scope's erasure trail. Unknown metadata is preserved; the supported
wire representation and explicit block-YAML conversion are in `migration`.

`conversations/ID/turns.jsonl` holds format-1 message rows,
`projections.jsonl` holds log/digest/epoch spans, and `redactions.jsonl` holds
the erasure trail. Turn numbers never reset after erasure. Unknown provider
payload under message/tool-call `extra` survives round trips.

Mailbox bodies are format-1 JSON inside memory documents, with their own
occurrence, receipt, claim and wake state. `neosian schema state` prints the
JSON Schema for canonical documents, histories, turns, projections and mailbox
records. Timestamps are timezone-aware UTC on the wire. Money, where retained
in historical payloads, uses integer micro-USD.

Malformed records, future formats, case collisions and paths escaping the
root are refused. Cooperating local processes share a private root lock;
files are born private. Memory mutations and scope restores use a durable redo
journal so their documents, audit rows and change events recover together.
Conversation writes and whole-store batches retain their separate guarantees.
Network filesystems are unsupported. Pause writers for migration, maintenance
and consistent exports. Read `neosian docs changes` before upgrading writers.
Postgres is the backend for distributed workers.

## Local operation budgets

Budgets are positive decimal integers supplied to the process performing the
operation. Unset or empty values use the defaults. Invalid values and exceeded
budgets refuse explicitly; they never truncate results or delete retained data.

| Setting | Default | Maximum accepted | Applies to |
|---|---:|---:|---|
| `NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS` | 10,000 | 100,000 | Selected knowledge import/check/export documents |
| `NEOSIAN_KNOWLEDGE_MAX_BYTES` | 256 MiB | 2 GiB | Knowledge content including normalized headers |
| `NEOSIAN_MAX_TEMP_BYTES` | 512 MiB | 8 GiB | Each bulk staging area or recovery-plan preparation; exchange charges both together |
| `NEOSIAN_REPLICA_MAX_PATHS` | 100,000 | 1,000,000 | Distinct retained receiver paths, including deleted history |
| `NEOSIAN_REPLICA_MAX_BYTES` | 1 GiB | 64 GiB | Retained receiver documents, version rows and erasure trail |
| `NEOSIAN_MAX_FILE_BYTES` | 64 MiB | 1 GiB | Decoded FileStore memory documents/version files, new memory transaction artifacts, exchange artifacts and Postgres search candidate content plus JSON extras |
| `NEOSIAN_MAX_RESPONSE_BYTES` | 64 MiB | 1 GiB | Each HTTP storage-client response, before JSON decoding |

For byte settings, 1 MiB is 1,048,576 and 1 GiB is 1,073,741,824. Larger values
allow more work and require more disk or memory; these are operation budgets,
not process RSS quotas. File pages retain bounded names and load their selected
documents; directory enumeration still scans names. Knowledge scans retain plan
and result metadata proportional to their document budget. Postgres search uses
one query snapshot and fetches one candidate at a time, stopping at page
lookahead; it remains a scan without a search index. HTTP page cursors are
validated, and every page still requires current server permissions.

Existing server request limits, per-document knowledge bounds, skill/context
budgets and archive behavior remain separate. Client limits cannot raise a
server limit or grant authority. Full-history archives and other whole-store
operations are not covered by the incremental knowledge workflow. A lower file
budget may refuse an existing large history: raise the budget explicitly, never
remove its rows. Lowered preparation budgets do not block recovery of an already
committed redo plan. Keep that plan intact and let the next storage operation
recover it before reading or writing.

## Postgres

The binary uses schema 4. Generate SQL with `neosian schema postgres` or
`neosian schema postgres --schema custom`, review it, and apply it with your
own database connection. Opening or serving a store never issues DDL. The
operator owns credentials, schema changes, transaction policy and backups.
The DSN and TLS compatibility rules are explicit in `neosian docs migration`.

## Archives and capabilities

`export DIR` writes a portable FileStore archive. `import DIR` preflights all
selected target scopes and conversations for occupancy, then restores their
content, histories, actors and erasure trails. A history-only or trail-only
unit is occupied. An I/O failure can leave a partial import; it is not a
multi-unit transaction. Existing format-1 archives remain readable.

Wire capabilities independently advertise paging, erasure and optimistic
concurrency. Built-in stores support all three and whole-store portability.
A base custom service can lack optional capabilities and must refuse them
with the documented errors. Mailbox mutation needs cross-worker optimistic
concurrency. Cursors are opaque and listing-specific; see `neosian docs wire`.

## Authoring a backend

Keep `assets/contracts/wire-v6.openapi.json` beside `state-v1.schema.json`
for offline reference resolution. They ship in the versioned docs artifact.
Implement the HTTP route envelopes, validation, attribution, scope allowances,
ordering, paging and failure behavior. JSON shape compatibility alone is
insufficient. Never stamp a restore's actor with the importing caller.

Run the same keyless runner used for the reference backends:

```sh
make install
make test-storage-contract backend=file
NEOSIAN_TEST_POSTGRES_DSN='postgresql://...' make test-storage-contract backend=postgres
NEOSIAN_TEST_SERVER_URL='http://127.0.0.1:6367' NEOSIAN_TEST_SERVER_TOKEN='test-token' make test-storage-contract backend=http
```

Use a disposable service: the runner writes random namespaces and tests erasure.
`profile=full` requires the base paging, erasure, concurrency and portability capabilities; `profile=base` exercises the advertised
subset and checks refusals. `NEOSIAN_TEST_SERVER_PEER_URL` optionally points at
a second worker on the same data to exercise cross-worker races. Without it,
HTTP mode uses one endpoint. The file and Postgres fixtures use two processes.
The runner imports no Python product; Python is test tooling only.

## Current-memory search

FileStore and Postgres advertise optional memory-search version 1. They share
keyword matching over current documents, with explicit metadata selection
and bounded excerpts. Files hold the root lock during a search page; Postgres
uses a statement snapshot. No persistent search index, cache or DDL is added.
Custom services declare support separately and pass the optional independent
search cases. See `neosian docs memory` and `neosian docs wire`.

## Current-document knowledge bundles

`knowledge import` and `knowledge export` use mounted reads/writes on every
backend. Their readable metadata is ordinary versioned content; storage
format 1, wire 6 and schema 4 are unchanged. Optional collection validation
runs in this workflow and explicit checks, not inside arbitrary raw writes.
Bundles carry current documents and source envelope evidence, while top-level
archives retain complete histories and original storage identities. Apply and
export require paused writers; no batch transaction or synchronization is
claimed. See `neosian docs knowledge` for format and provenance limits.

Managed skills and supporting text use ordinary memory rows and revisions.
Profile pins are portable configuration guards; dependencies and resource hashes
are content metadata. No backend capability or DDL is added. Full archives
retain skill histories, while skill directory exports carry current content
and source evidence. Apply/export requires paused writers.

Reviewed upkeep composes existing reads, revisions, writes and deletes. Its
format-1 request/proposal files are an application contract, not a new stored
record or wire capability. Changes preserve envelope extras and source
provenance, with normal actor attribution and version fences on replacements.
Pause writers: deletes and erasure have no conditional fence, and batches can
partially apply. See `neosian docs memory` for inspection and recovery.

## Reviewed shared knowledge

Governance format 1 lives in the ordinary document body and shares its versions
and erasure. It introduces no storage tables, DDL or sidecar. The optional
knowledge-policy version-1 capability filters current memory search before
matching and pagination. Postgres's existing generated FTS column also clears
when content is erased; it is not used by the lexical memory-search API.
Raw authorized reads/history and faithful archives retain their inspection and
restoration contracts. Expiry is a retrieval condition, never automatic deletion.
Read `neosian docs governance` for review assertions and separate copy erasure.

## Durable changes

Files advertise change-feed version 1 and keep reference-only events under each
scope's `.changes/`, with crash recovery in the root `.transactions/` journal.
Postgres schema 4 remains valid without a feed; `schema changes-postgres` prints
optional operator-owned extension 1. Pause and restart all writers when enabling
it. The core never applies DDL. Mutations and their events share a Postgres
transaction; interrupted file mutations replay before subsequent storage calls.
Archives carry state and history, with a fresh restoration marker at the target.
Polling, retention gaps and mailbox references are specified in `neosian docs
changes`. Custom services declare the optional capability and run its additional
conformance cases; the base/full storage profiles do not force optional features.

## Incremental replicas

`exchange` reads current ordinary documents from one authorized tracked scope
and applies snapshots/deltas to a dedicated local FileStore. The source may use
files, Postgres with changes extension 1, or capable HTTP. Pause source writers;
receiver data and `.exchange/receipt.json` recover in one redo transaction.
Local edits conflict, and missing checkpoints or source gaps require a new empty
replica. Source history bodies and raw turns are excluded; full archives keep
their separate restore contract. See `neosian docs exchange`.
