---
title: Reliable incremental exchange
summary: One-way current-memory snapshots and deltas, durable checkpoints, conflicts and erasure
---

# Reliable incremental exchange

`neosian exchange` copies one explicitly selected, authorized memory scope to
a dedicated local FileStore replica. Start with a snapshot, then carry deltas.
The source can be files, Postgres with changes extension 1, or an HTTP service
advertising change-feed version 1. The receiver is an explicitly named local
directory, with the same scope name as the source.

This direction supports memory, skills, collection definitions and reviewed
knowledge. It has no remote apply endpoint, background scheduler or bidirectional
merge. A host can run the CLI beside its receiver. Using one shared remote
workspace through a profile remains available without making replicas.

## Snapshot and delta workflow

Pause every source writer before each export, including recording hooks. Pass
`--paused` to confirm that condition; the exporter also checks the stream head
before and after reading. Keep writers paused until export returns. Direct file
edits and old writers cannot provide continuity. An untracked source must first
receive a compatible memory mutation; an identity is never invented on export.

```sh
neosian exchange snapshot /memories snapshot.json --scope user:demo --paused
neosian exchange inspect snapshot.json
neosian exchange apply snapshot.json --root ./replica --scope user:demo
neosian exchange apply snapshot.json --root ./replica --scope user:demo --write

# After further source mutations, pause its writers again:
neosian exchange delta /memories delta.json --after snapshot.json --scope user:demo --paused
neosian exchange apply delta.json --root ./replica --scope user:demo
neosian exchange apply delta.json --root ./replica --scope user:demo --write
neosian exchange status --root ./replica --scope user:demo
```

Source commands accept the normal `--profile`, `--mount`, `--scope`, `--root`,
`--url` and Postgres selection. The positional source path must name exactly one
mount root. A read-only mount can export. Receiver commands always require an
explicit `--root` and `--scope`; they do not resolve profile bindings or remote
destinations. Preview validates the entire operation without installing data.
`--write` authorizes applying the reviewed file under local filesystem authority.

Exports create private UTF-8 JSON files and refuse existing output paths. Inspect
validates the bundle and reports paths, present/deleted/erased state, versions,
erasures and checkpoints. Read the JSON file to review actual content and metadata.
All exchange commands print JSON. A checksum identifies content, not a trusted
sender: obtain bundles through a trusted channel and review before applying.

## What travels

Snapshots carry current ordinary documents with source versions, actors, UTC
creation/update timestamps, redaction flags and opaque JSON envelope metadata.
Markdown bytes, knowledge metadata, governance assertions and skill resources
remain unchanged. JSON metadata uses the existing wire representation; unsupported
YAML metadata refuses rather than being silently converted. An approval label
remains an assertion, and source references retain their original scope/path.

Source revision bodies, deleted-document history, sessions, handoff, mailbox
records, raw conversation turns, configuration, bindings and endpoint credentials
are excluded. This workflow never reads conversation APIs or opts into capture.
Ordinary document content and opaque metadata are copied as authorized data;
they are not scanned for secrets. A profile's governance selection policy does
not filter this raw inspection export. Use separately authorized scopes when
private drafts or metadata must not travel.

The receiver retains only the current revisions it actually observes. Version
numbers can have gaps: observing v2 and then v8 does not import v3 through v7.
The first observed revision gets a receiver `created` history row; later observed
live revisions get `modified` rows with source version/actor/update timestamp.
Deletion adds a content-free skeleton with its source version, actor and time;
previously observed revisions remain until erased. Full archives retain the
separate faithful history/envelope restoration contract.

A delta consumes all events from its base checkpoint through one stable head.
Multiple writes to a path coalesce to its current state. Rename carries both the
old path's deletion and the destination's current state; paths retain their own
version sequences. Erasure travels independently of version and timestamp.
Scope erasure clears every retained replica body, including deleted paths, before
installing current observations written after erasure. Intermediate source bodies
are never copied merely because an event mentioned their versions.

## Checkpoints, conflicts and recovery

Source identity is the exact scope plus its persistent S7 stream UUID. Endpoints,
local usernames, profile labels and accounts do not establish identity. Checkpoint
cursors are opaque and never grant authority. Every source read checks current
permissions; a previous export does not authorize the next one.

Initial snapshots require an empty receiver scope: no documents, history,
redaction trail or prior change tracking. Deltas require the exact stored base
checkpoint and source identity. Only the identical last bundle replays as a
`duplicate` no-op. Older, reordered and overlapping bundles refuse with
`checkpoint_mismatch`; they cannot roll a replica back to pre-erasure content.

The receiver holds its root lock while checking and applying. Documents,
observation history, erasure trail, a private `.exchange/receipt.json` and a local
change-feed reset marker share one redo transaction. A lost response may mean
the entire apply committed. The next storage operation recovers it before reading;
retrying the same file then reports `duplicate`. Local replica feeds announce
reconciliation, not copies of source event IDs.

Status reports `ready`, `empty`, `conflict` or `snapshot_required`. A full artifact
fingerprint detects local writes, deletes, redaction and metadata edits, including
changes that do not increment a document version. Concurrent local writers
serialize with apply; a changed replica refuses with `replica_changed`. There is
no automatic merge or conflict override. Preserve local work separately before
starting a new empty replica.

If the last bundle file was lost, recover its checkpoint from the receiver:

```sh
neosian exchange status --root ./replica --scope user:demo --checkpoint-only > checkpoint.json
neosian exchange delta /memories next.json --after checkpoint.json --scope user:demo --paused
```

If the receiver receipt itself is missing or damaged, occupied data cannot be
adopted by guessing a version or applying an old snapshot. Preserve it for
inspection and start a fresh snapshot in a new empty replica. Keep receipts with
the replica in paused-writer backups. Unknown or corrupt recovery plans refuse;
do not remove them to force access.

Source retention gaps, resets, replaced stream identities and intervals over
10,000 events require a fresh snapshot. Export reports `snapshot_required` and
does not produce a partial delta. A fresh snapshot goes into a new empty receiver;
the old replica remains explicitly stale until you replace or dispose of it.

Erasure clears all affected replica observation bodies and keeps audit skeletons.
Opaque envelope metadata follows the existing storage contract and is retained;
governance/provenance metadata inside erased content is cleared. New source
revisions after erasure can contain new data. Separately copied bundles, backups,
renamed copies and derived content need their own erasure. An offline receiver
cannot know about an erasure that has not been delivered to it.

## Format and limits

`neosian schema exchange` prints `exchange-v1.schema.json`. A bundle has format 1,
an `id`, optional `base` (null for a snapshot), `checkpoint`, `updates` and
`erasures`. Each update has a path and exactly one current document or deletion
skeleton. Checkpoints have format 1, scope, stream and cursor. No URLs, binding
paths or bearer credentials are included.

The identifier is lowercase SHA-256 over compact UTF-8 JSON after removing `id`
and recursively sorting object keys, with literal Unicode and the product's JSON
number encoding. Whitespace and input object order do not affect it. Consumers
can preserve the exported numeric representations or use `exchange inspect` to
verify. This digest is not a signature or authorization capability.

Bundle bounds remain 1,000 selected paths, 1 MiB per body, 20 MiB per serialized
bundle, 10,000 events per delta and 2,048 UTF-8 bytes per cursor. A replica may
grow beyond 1,000 paths across explicit deltas. Its default budgets are 100,000
retained paths and 1 GiB of document/history/trail artifacts; use
`NEOSIAN_REPLICA_MAX_PATHS` (up to 1,000,000) and `NEOSIAN_REPLICA_MAX_BYTES` (up to
64 GiB) to change them. The receiver hashes artifacts incrementally and stages
changed files on disk; untouched bodies are not copied into the apply plan.
Private receiver stages live under its transaction directory; recovery removes
leftover stages from interrupted applies after completing any committed plan.
`NEOSIAN_MAX_FILE_BYTES` bounds each artifact (64 MiB by default), and
`NEOSIAN_MAX_TEMP_BYTES` bounds staged files plus the serialized redo plan
(512 MiB by default). A long per-path history can reach its file budget before
the whole replica reaches its budget. History is never pruned to fit.

These settings do not enlarge format-1 bundles. If a source snapshot or a single
required delta exceeds its bundle bounds, choose smaller independent scopes or
use the separate full archive workflow; there is no automatic snapshot splitting
or continuation within a delta. Status/apply still read and hash the entire
replica to detect local edits, so bounded memory does not mean constant time.

Unknown fields/formats, duplicate JSON keys, ambiguous case aliases, unsafe paths
and oversized inputs refuse. Serialized bounds include JSON escaping. Refusals
name the resource budget; adjust it explicitly or reduce the selected batch.
An already committed recovery plan completes even if preparation limits were
subsequently lowered. Existing storage format 1, wire 6, schema 4, receipt
fingerprints and error codes remain unchanged. Receivers must use compatible
binaries that understand the receipt recovery path; stop old writers before
upgrading. See `neosian docs stores` for all local operation budgets.
