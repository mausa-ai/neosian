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
files are born private. Neither the lock nor atomic file replacement promises
whole-store crash recovery or safe operation on network filesystems. Pause
writers for migration, maintenance and consistent exports. Postgres is the
backend for distributed workers.

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
`profile=full` requires every capability; `profile=base` exercises the advertised
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
