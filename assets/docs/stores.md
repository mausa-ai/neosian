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

## Record quotas and disk capacity

A host can reserve logical bytes against an account's quota before dispatching
a record write or restore. Use the UTF-8 byte length of each decoded field,
with zero for an absent or null field. In the table, `u` is that length,
`c = u(conversation_id)`, and `o` is the length of the draft or restore ID.

| Item | Logical bytes |
| --- | --- |
| Each record, including each chunk | `u(text) + u(call_id) + u(name) + 2*c + 256` |
| Write seal or restored unredacted header | `u(actor) + o + 4*c + 512` |
| Restored redacted header | `u(actor) + 2*c + 256` |
| Restored projection | `u(text) + 2*c + 128` |
| Restored erasure act | `u(actor) + 4*count(turns) + 2*c + 128` |
| Opening a draft or restore | `o + 2*c + 1024` |
| Seal or commit creating the conversation parent | `c + 256` |
| Restore commit marker | `o + 2*c + 256` |

For a conservative reservation, count every item sent as new. Include
bookkeeping when the part could open the draft or restore, and the parent
when a seal or commit could create it. Reserve the gross addition before
allowing for any refund. A seal can omit the actor pinned when the draft
opened. Use the opening actor's stamped UTF-8 length, including the client
prefix, or the maximum stamped length the host allowed at opening. A
restore uses the archived actors.

Settle each confirmed operation once with `stored_bytes - freed_bytes` and
release its reservation. A status probe adds zero. In a repeated part,
only new items incur item charges. Seal and commit release opening
bookkeeping. Discard and applied stale cleanup refund the work actually
removed. A cleanup preview changes nothing.

If a reply is lost, keep the reservation pending. Probe the original draft
or restore, or retry the identical part with the same ID. A zero-byte
replay can confirm success while leaving the original charge or refund
unknown. Reconcile that original change against durable operation records
and store state before settling the reservation.

Before dispatch, measure free space on the volumes serving the store and
allow headroom for concurrent writes and temporary work. FileStore JSON
escaping and staging, and PostgreSQL indexes, TOAST and WAL can grow
independently of logical charges. Under disk pressure a host can refuse new
growth and release the reservation for a request it knows was never sent.
A logical refund credits the account immediately on settlement. Disk space
can remain allocated for reuse.

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

A FileStore keeps turns written in parts only after `neosian records enable`
writes `.records/format`; it enables a store that exists and refuses a
`--root` that does not, rather than create one. Such a turn keeps a format-2 header row in
`turns.jsonl`, in the same number sequence as whole turns, and its records in
`conversations/ID/records/TURN.jsonl`, one line each with its digest. A turn
still being written is a draft under `conversations/ID/.drafts/`, which no
turn read, numbering or conversation listing sees until its seal. `read_turns`
assembles a header's turn whole, up to `NEOSIAN_MAX_TURN_BYTES`, and refuses
a longer one by name. History search matches such a turn as its whole messages and leaves
one past `NEOSIAN_MAX_TURN_BYTES` out of the answer, counted in `omitted`,
counting its words from its records a record at a time. In a store without the
marker a format-2 row is refused as newer than supported, as an older binary
refuses it, and a seal marker left in a conversation's `.drafts/` is refused
by name: enable records to finish that seal, or remove `.drafts/` to drop
it. Numbering reads the log's last line
alone, and the first stamp its first line.

For a draft or published record, the JSONL line occupies the byte length of
the canonical write record plus 96 bytes for the format field, SHA-256 field
and line feed. The canonical record includes JSON escapes for quotation
marks, backslashes and control characters. The logical charge uses the
unescaped UTF-8 field lengths. A directory also holds headers, draft state
and other metadata.

A record seal or restore commit charges the conversation parent once. A
committed empty restore already holds that parent. If full erasure removes
its last receipt, a zero-length `redactions.jsonl` can retain the parent.
It contains zero erasure acts. The conversation appears in listings once
it has a turn or projection. A wholly empty legacy FileStore restore leaves
no retained state, so the first record publication there charges the parent.

Run `neosian records drafts` to preview cleanup across a store that keeps
records. A draft or open restore qualifies once it has gone untouched for
more than `NEOSIAN_DRAFT_SECONDS` (one day by default). Add `--discard` to
remove those items. Both commands print one JSON line per affected
conversation, in conversation ID order. Each line gives `conversation_id`,
the counts `drafts` and `restores`, and `freed_bytes` measured by the same
byte accounting used when records are written. An empty result prints
nothing, and `--json` prints the same lines. Use `--root PATH` to select an
existing FileStore, `NEOSIAN_POSTGRES_DSN` (with `--schema`) for PostgreSQL
with records extension 1 or 2, or `--url URL` with an operator token in
`NEOSIAN_CLIENT_TOKEN` for a server that declares turn records version 1.
The command never selects a profile. It asks for fifty conversations at a
time through `store/sweep_records` or the store's own sweep, and each store
prints the same lines. It stops with an error when a page names a next
cursor without a row, names the cursor it was sent, repeats or reorders a
conversation, or answers another `write` than it was asked. An error on a
later page is reported after the lines already printed; with `--discard`
those conversations were already cleaned, and running the command again
continues with the rest. A preview of a FileStore may finish a discard that
an earlier `--discard` authorized and journalled but did not complete: the
store's own recovery removes the items that discard had already chosen,
and nothing else.

Run `neosian records migrate` on a records-enabled FileStore to convert
existing whole turns to records. Record reads and record searches can then
process those turns in parts. The legacy `read_turns` route still assembles
each requested turn up to its configured size limit. Migration is optional.
The command migrates every conversation by default. To select conversations,
repeat `--conversation ID`. The names are
checked first: one that is not a conversation of the store refuses the run
before any conversation is touched. The command prints one JSON line per
conversation, in ID order or in the order named:

- `conversation_id`
- `migrated`: whole turns this run turned into records
- `records`: turns that were records already
- `skeletons`: erased turns, which hold nothing to migrate
- `kept`: whole turns left as they were, counted by reason

A conversation is fully migrated when every count in `kept` is 0. A repeat
prints `migrated` 0 and changes nothing.

A turn is migrated only when its records give its stored line back byte for
byte, so turning it back into a whole turn restores the same line. Every
other row keeps its bytes and its place, and is still read whole:

- `spelling`: the line spells its turn otherwise than neosian writes it:
  other spacing, key order or escapes, a carriage return, a key left to its
  default, or a last line without its newline.
- `content`: the line or one of its messages holds a key neosian does not
  carry, or a tool call id, tool name or `tool_call_id` over 1,024 bytes,
  which records cannot carry.
- `size`: the turn is over `NEOSIAN_MAX_RECORD_TURN_BYTES`. A later run
  migrates it once the setting is raised.
- `number`: a file for the turn's number is already under `records/`.

A turn longer than `NEOSIAN_MAX_TURN_BYTES` is migrated like any other.
Afterwards `read_turns` refuses it by name and history search leaves it out,
counted in `omitted`, until the setting is raised to at least the `bytes` of
its header row. A client that reads records reads it in parts either way.

Migration holds the store's lock for one conversation at a time, so other
calls on the store wait while that conversation is rewritten. It holds one
turn in memory at a time. Allow free space for the staged log and record
files alongside the original log until publication. The new files are written under
`conversations/ID/.migrate-preparing/` and published by renaming that
directory to `.migrate-pending`. A run stopped before the rename changed
nothing. A run stopped after it is finished by the next call on that
conversation, from any process. Use `--root PATH` to select an existing
store.

Run `neosian records fold` to turn every turn stored as records back into a
whole turn. It is the first step of going back to a store without records.
It folds every conversation, or only those named with `--conversation ID`,
which repeats; the names are checked first, as for `records migrate`. A
conversation that holds only drafts, or only a staged restore, is covered
too, though no listing shows it. The command prints one JSON line per
conversation, in ID order or in the order named:

- `conversation_id`
- `folded`: turns stored as records that this run turned back into whole
  turns
- `whole`: turns that were whole already
- `skeletons`: erased turns
- `drafts` and `restores`: open drafts and staged restores that
  `--discard-pending` removed

Every whole row keeps its bytes and its place, so folding a migrated
conversation gives its first log back byte for byte. A turn that was written
as records becomes the row its messages make.

An open draft or a staged restore is pending work. A conversation holding
any is refused by name, with both counts, and nothing in it changes. Let the
work finish, or pass `--discard-pending` to remove it, whatever its age. It
is removed only after the whole conversation has been checked, so a refusal
for another reason consumes none of it.

A turn longer than `NEOSIAN_MAX_TURN_BYTES` refuses its conversation by name
before the turn is assembled, and nothing in the conversation changes. Raise
the setting for the run and fold again.

A fold holds the store's lock for one conversation at a time and one turn in
memory at a time. Allow free space for the new log alongside the records
files until publication. The new log is written under
`conversations/ID/.fold-preparing/` and published by renaming that directory
to `.fold-pending`. The log is put in place before any records file is
removed. A run stopped before the rename changed nothing. A run stopped
after it is finished by the next call on that conversation, from any
process.

While the store still keeps records, a draft sealed before the fold can
still be confirmed. The fold keeps the SHA-256 of each of that draft's
records, and none of their text, in `conversations/ID/.drafts/folded/TURN`,
beside the seal's own receipt. A repeat of the seal is answered as before,
in the parts first sent. Erasing the turn removes both.

To finish going back, stop every process that uses the store, fold every
conversation, then remove `.records/` and every `conversations/ID/.drafts/`.
An older neosian then reads every turn. Until then it refuses the store's
format-2 rows by name.

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
| `NEOSIAN_MAX_TEMP_BYTES` | 512 MiB | 8 GiB | Each bulk staging area or recovery-plan preparation (exchange charges both together), and the part of one served answer spooled to disk |
| `NEOSIAN_REPLICA_MAX_PATHS` | 100,000 | 1,000,000 | Distinct retained receiver paths, including deleted history |
| `NEOSIAN_REPLICA_MAX_BYTES` | 1 GiB | 64 GiB | Retained receiver documents, version rows and erasure trail |
| `NEOSIAN_MAX_FILE_BYTES` | 64 MiB | 1 GiB | Decoded FileStore memory documents/version files, new memory transaction artifacts, exchange artifacts and Postgres search candidate content plus JSON extras |
| `NEOSIAN_MAX_RESPONSE_BYTES` | 64 MiB | 1 GiB | Each HTTP storage-client response, before JSON decoding; on Postgres, each row of every read, as its compact JSON |
| `NEOSIAN_SEARCH_MAX_CANDIDATES` | 100,000 | 1,000,000 | Turns or documents holding a term in one relevance search, history or memory, on files and Postgres |
| `NEOSIAN_MAX_PAGE_BYTES` | 8 MiB | 1 GiB | The largest `page_bytes` a size-paged request is served; a larger request gets pages of this size |
| `NEOSIAN_DRAFT_SECONDS` | 86,400 | 2,592,000 | How long an untouched draft of a turn written in parts is kept; at least 60 |
| `NEOSIAN_MAX_RECORD_TURN_BYTES` | 1 GiB | 16 GiB | The largest turn kept as records, by the UTF-8 bytes of its record texts; at least 65,536 |
| `NEOSIAN_MAX_TURN_BYTES` | 64 MiB | 1 GiB | The largest turn kept as records that `read_turns` assembles whole; at least 65,536 |

For byte settings, 1 MiB is 1,048,576 and 1 GiB is 1,073,741,824. Larger values
allow more work and require more disk or memory; these are operation budgets,
not process RSS quotas. File pages retain bounded names and load their selected
documents; directory enumeration still scans names. Knowledge scans retain plan
and result metadata proportional to their document budget. Path-ordered memory
search on Postgres fetches one candidate at a time and stops at page lookahead.
Ranked search, history or memory, keeps a few facts per candidate (its key,
term counts and length), never its content, then reads the `limit` winners
again: on files under the same lock, on Postgres in the same read-only
REPEATABLE READ snapshot, with the counting done while the rows stream so that
statement and transaction timeouts bound it. Newest order holds at most
`limit` keys and on Postgres is one ordered, limited statement. Search remains
a scan without a search index. A relevance search over more candidates than
`NEOSIAN_SEARCH_MAX_CANDIDATES` refuses with `value_error` and the message
`resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: more than N turns hold
a search term; narrow the words or the time range` (documents: the words, the
path or the update dates); raise the setting or narrow the query. Each
relevance candidate costs its facts, 40 bytes plus 4 per query term (up to
twice that while the arrays grow), and the name it is read back by: a history
candidate shares its conversation's id with the other candidates of that
conversation (on files the store's own listing of conversations, on Postgres
one copy of each id plus about 100 bytes for its lookup), and a memory
candidate keeps its path, up to 512 characters, packed into shared blocks.
Measured at the default bound
with one candidate per conversation and 128-character ids, a history search
peaks at about 33 MiB on files and 43 MiB on Postgres; with 500-character
memory paths, about 67 MiB. At the maximum of 1,000,000 that is roughly 0.3
and 0.6 GB. Every Postgres read checks each row alone against
`NEOSIAN_MAX_RESPONSE_BYTES`, measured as the row's compact JSON: rows are
never charged together, so any read 2.16.0 answered is answered, served or
local. A local command that needs a whole conversation in its own process
(session start, `neosian continue`, `recall_turn`'s read, audit, export,
erasure) holds it there, as 2.16.0 did. HTTP page cursors are validated, and
every page still requires current server permissions.

One refusal remains where no smaller answer exists: a single record larger
than a budget, a Postgres row whose JSON passes `NEOSIAN_MAX_RESPONSE_BYTES`
or one item larger than the server's spool budget. What bounds a record on
the way in: every HTTP request is at most 64 MiB, so a turn, projection or
memory document written through the wire is at most about that much JSON (a
turn's stored form can be slightly larger, as messages are normalized); the
file store refuses a memory write past `NEOSIAN_MAX_FILE_BYTES` (64 MiB). A
turn recorded by local hooks has no total bound (each tool result is cut to
1 MiB of text), and a local write to Postgres is not size-checked, nor are
restores or direct SQL. A record over the default budgets can therefore come
only from those paths or from a budget lowered after it was written; raise
the setting to read it.

`neosian serve` writes answers that grow with stored content item by item into
a spool while it reads the store (in memory while the answer is at most 1 MiB,
then the whole answer in a private temporary file under
`NEOSIAN_MAX_TEMP_BYTES`), releases the store, then sends the spool with its
length (`neosian docs wire`, "Spooled answers"). An answer past that budget is
refused; the binary then asks again in smaller pieces (size pages, smaller
listing pages), so its reads and searches are answered, while other clients
can do the same with `page_bytes` or `limit`. Searches and turn reads come
from the store a turn at a time (a file log a line at a time, a Postgres
statement as one stream), so a request holds about one turn whatever the
answer's size, and every refusal is an ordinary response. Memory version and
history pages hold their page (up to 500 versions with content), and on files
the document's whole journal or the scope's whole history for every page, as
2.16.0 did; a listing asked for no rows reads none. The hosted `/mcp`
`continue_session` holds the whole conversation it continues. A slow client
costs an open file and disk, never the store lock or a database connection.

Give the server a writable, disk-backed temporary directory (`TMPDIR` on
Unix, `TMP` on Windows). A
memory-backed one (tmpfs) counts against a container's memory limit and
defeats the bound; the spools' file cache on disk also counts there but is
reclaimed under pressure. When no spool can be made or written, answers over
1 MiB are held in memory, as 2.16.0 held them, up to
`NEOSIAN_MAX_RESPONSE_BYTES` each, and refused past that; `neosian serve` says
so once at startup. Disk use is the answers being sent at once, each at most
`NEOSIAN_MAX_TEMP_BYTES`; only the process's open-file limit bounds how many
are sent at once (each holds a socket and a spool file), and no timeout ends
a stalled send. Each server's spools sit under its own locked
`.neosian-serve-<uuid>` directory, removed when it stops. On Unix, startup
cleans up a killed server's spools in the same temporary directory after
checking ownership and the server lock. On Windows, the operator cleans up
after a killed server. `NEOSIAN_MAX_PAGE_BYTES` caps the ceiling a size-paged
request may ask for.

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

Schema 4 can also store turns written in parts. To enable records extension
1, pause writers, review the output of `neosian schema records-postgres
--schema NAME`, apply it with the schema owner's connection, then restart
compatible writers. The command defaults to the `neosian` schema. Each store
caches the installed records version until it restarts. A partial installation
or an unsupported marker version produces a named configuration error.

With `NEOSIAN_POSTGRES_DSN` set to the operator's connection, run
`neosian records migrate --schema NAME` to convert existing whole turns
online. Use binaries that support the installed records version for every writer.
Add `--conversation ID` to select one conversation, or repeat it for
several. The command checks every requested name before converting a turn.
Each turn commits separately. Readers see the old turn or the complete
converted turn. After an interruption, rerun the same command to continue.

Each output line reports `conversation_id`, `migrated`, `records`,
`skeletons` and `kept`. PostgreSQL compares the stored JSONB representation
and the original history search text before converting a turn. For example,
a missing default key counts under `kept.spelling`. A turn from an older
schema whose search text was never populated counts under `kept.content`
and keeps its original history search behavior. The `size` and `number`
counts cover a turn over the configured record size or one whose number
already has a record mapping.

To fold migrated or restored turns back into ordinary schema-4 rows, set
`NEOSIAN_POSTGRES_DSN` to the operator connection and run
`neosian records fold --schema NAME`. Optional `--conversation ID` flags
select exact names. The default includes conversations whose only data
is an open draft or a staged restore. Each conversation commits in one
transaction. An interruption rolls back the current conversation. Rerun
the command to finish. Each output line counts converted turns, existing
whole turns, erased turns, discarded drafts and discarded restores.

Folding reconstructs the messages and history search text. Existing whole
rows keep their original JSONB and search text. A committed restore keeps
its replay receipt. An open draft or staged restore requires either
finishing that work first or passing `--discard-pending`. A turn above
`NEOSIAN_MAX_TURN_BYTES` requires a higher limit. If the conversation
contains a turn sealed by an agent's native record write, extension 1
cannot retain its original retry digests during conversion. The command
identifies the turn and rolls back the conversation, including any
requested discard. Upgrade to extension 2 to fold those native writes.

### Folding native writes with records extension 2

On a schema with records extension 1, pause all writers, including servers
and direct CLI clients. Review the SQL printed by
`neosian schema records-postgres --version 2 --schema NAME` and apply it
as the schema owner in the target database. The upgrade installs two
receipt tables and sets the version to 2 in a single transaction. Applying
the script again checks the installation. To start with a new schema,
install reference schema 4 and records extension 1 first.

Give each runtime role its ordinary table privileges on the two new tables.
For example, for the default schema and a writer role named `neosian_writer`:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON neosian.record_seals,
    neosian.record_seal_pages TO neosian_writer;
```

Restart all processes using this database with a version-2-compatible
binary. A native fold keeps the original write's draft ID, turn number,
actor, timestamp, record count and byte count in `record_seals`. Each
`record_seal_pages` row holds up to 32 original SHA-256 digests in sequence
order. A client may retry any previously accepted range. Matching records
return the original sealed header with zero additional bytes stored or
freed, including after re-migration and a second fold. A changed record
gets the existing draft-mismatch response. Selected erasure removes that
turn's receipt and pages in the same transaction as the turn body and trail.
Full erasure removes them throughout the conversation.

Both tables are keyed by conversation ID and turn number. Operators can
inspect digest storage for a conversation in the default schema:

```sql
SELECT count(*) AS pages, coalesce(sum(octet_length(digests)), 0) AS digest_bytes
FROM neosian.record_seal_pages WHERE conversation_id = :'conversation_id';
```

The local fold command reports counts per conversation. The digest byte
total measures the original hashes held for retry. PostgreSQL relation
sizes also include the rows, indexes and space available for reuse.

### Returning from extension 2 to whole turns

Back up the database and stop every process using this store. Finish any
pending writes or restores and run `neosian records fold --schema NAME`
across all conversations. Use `--discard-pending` only if you intend to
discard unfinished work. Inspect the fold result before continuing.

Removing the extension keeps conversations, whole turns, projections and
erasure history. It deletes the receipts used to confirm old writes and
restores. To proceed, run this transaction as the schema owner with
`psql -v ON_ERROR_STOP=1`. Replace `neosian` with your schema name.

```sql
BEGIN;
LOCK TABLE neosian.neosian_schema, neosian.turns, neosian.neosian_records,
    neosian.record_drafts, neosian.record_turns, neosian.record_staging,
    neosian.turn_records, neosian.record_seals, neosian.record_seal_pages
    IN ACCESS EXCLUSIVE MODE;
DO $guard$
BEGIN
    IF (SELECT array_agg(CASE WHEN singleton THEN version END)
        FROM neosian.neosian_schema) IS DISTINCT FROM ARRAY[4]
       OR (SELECT array_agg(CASE WHEN singleton THEN version END)
        FROM neosian.neosian_records) IS DISTINCT FROM ARRAY[2] THEN
        RAISE EXCEPTION 'expected reference schema 4 and records extension 2';
    END IF;
    IF EXISTS (SELECT FROM neosian.turns WHERE neosian_format IS DISTINCT FROM 1)
       OR EXISTS (SELECT FROM neosian.record_turns)
       OR EXISTS (SELECT FROM neosian.turn_records)
       OR EXISTS (SELECT FROM neosian.record_staging)
       OR EXISTS (SELECT FROM neosian.record_drafts
                  WHERE (kind, state) IS DISTINCT FROM ('restore', 'committed')) THEN
        RAISE EXCEPTION 'complete the fold and settle pending work first';
    END IF;
END
$guard$;
DROP TABLE neosian.record_seal_pages, neosian.record_seals, neosian.record_staging,
    neosian.turn_records, neosian.record_turns, neosian.record_drafts,
    neosian.neosian_records;
DROP FUNCTION neosian.record_seal_page_count(), neosian.record_seal_page_immutable();
COMMIT;
```

After the transaction commits, the store can reopen with a binary that
reads whole turns on reference schema 4.

### PostgreSQL restore and upkeep

During a restore, each record keeps its source turn number in `part`.
Staged rows stay out of conversation listings and search until publication.
A status probe reports how far the restore has reached. Repeating accepted
items leaves its byte charge unchanged. Discarding an open restore releases
its recorded charge and removes its staged rows.

A restore commit checks that the target still has no visible turns or
projections. It publishes the headers, projections and erasure trail in one
transaction. The records keep their original rows. A retry of the final
request returns the saved position and digest, including after a restart.

The PostgreSQL store previews stale work in bytewise conversation ID order.
Each page covers up to 100 conversations. It includes open writes and
restores whose last touch was more than `NEOSIAN_DRAFT_SECONDS` ago, even
if their conversation has no published turns. Discard rechecks eligibility
under each conversation's writer lock. A draft refreshed or sealed while
cleanup waits is preserved. The reported `freed_bytes` is the logical
charge of the owners actually removed. A retry skips entries already
removed, and committed restore receipts remain available for replay.

Erasing a stored turn removes its mapped records and their digests, blanks
covering projections, and keeps its number, timestamp and actor. Other turns
from the same restore keep their records. A full `neosian redact ID --all`
also clears that conversation's pending writes, staged restores and
committed restore receipts. A fresh search sees the erasure immediately.

Record search examines the text of each record and the name of each tool
call. An occurrence split between records produces no hit. Older whole
turns are converted into records during a search. Both formats contribute
to the same relevance scores and candidate limit. PostgreSQL uses `ILIKE`
for membership and Unicode lowercase for scoring.

For frequent substring searches on stored records, the schema owner can
install `pg_trgm` and add a GIN index on each searched column. Terms of
three or more characters can use these indexes. These commands use the
default schema.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX turn_records_text_trgm ON neosian.turn_records USING gin (text gin_trgm_ops);
CREATE INDEX turn_records_name_trgm ON neosian.turn_records USING gin (name gin_trgm_ops);
```

### Records extension 1 operator contract

| Table | Stable columns | Meaning an operator may rely on |
|---|---|---|
| `neosian_records` | `singleton`, `version` | the extension is installed; `version` is 1 |
| `turn_records` | `conversation_id`, `owner`, `part`, `seq`, `kind`, `call_id`, `name`, `size`, `text` | one row per record; `text` is the record's text exactly as answered; a row belongs to its `conversation_id` whether visible or not |
| `record_turns` | `conversation_id`, `turn`, `owner`, `part`, `draft`, `records`, `bytes`, `created_at` | one row per visible format-2 turn; the records with the same (`conversation_id`, `owner`, `part`) are that turn's |
| `record_drafts` | `conversation_id`, `owner`, `kind`, `name`, `state`, `touched_at`, `bytes` | open drafts and restores (`state` open) and committed restores; `bytes` is what it holds by the size rule |
| `record_staging` | `conversation_id`, `owner`, `item`, `body` | a staged restore's headers, projections and acts until commit |

The other columns (`message`, `role`, `content`, `block`, `call`, `chunk`,
`last`, `digest`, `neosian_format`, `actor`, `turn_bytes`, `opened_at`,
`position`, `ord`) are the core's and change meaning only with a new
extension version. An operator **may** read every table; delete every row
of a conversation in one transaction across the four record tables and
schema 4's `turns`, `projections`, `turn_redactions` and `conversations`
(closing an account); add indexes, in particular
`CREATE INDEX ... ON turn_records USING gin (text gin_trgm_ops)` and the same
on `name`, which the predicates of item 6 can use for terms of three
characters or more; set storage parameters such as lz4 compression on
`text`; run `VACUUM`. An operator **must not** insert or update rows, or
delete part of a conversation's records: erasure is `redact_turns`, and
stale drafts and restores go through `store/sweep_records`. This query sums
the logical charge for one conversation's record rows.

```sql
SELECT sum(octet_length(text) + coalesce(octet_length(call_id), 0)
           + coalesce(octet_length(name), 0) + 2 * octet_length(conversation_id) + 256)
  FROM {{schema}}.turn_records WHERE conversation_id = $1;
```

Changes come only as records extension 2, with its own SQL, detection and
recorded decision.

For the calculation above, substitute the installed schema for `{{schema}}` and
bind the conversation ID to `$1`. Its byte lengths assume a UTF-8 database.
The sum covers the logical charge for record rows. For a conversation
with no record rows, the sum is SQL NULL.
Physical space includes indexes, TOAST storage and space available for reuse.

### Reconciling private and published records

An open write or restore holds its full logical charge in `record_drafts.bytes`,
including its private record rows and staged items. At commit, a restore's
owner row holds only the charge for its committed marker. Published record
payloads are selected by conversation, owner and part. One committed
restore can own parts for several turns.

This query reads three components in one snapshot on either records
extension version. In `psql`, set `conversation_id` to the exact ID.

```sql
WITH target AS (SELECT :'conversation_id'::text AS id),
owners AS (
    SELECT coalesce(sum(d.bytes) FILTER (WHERE d.state = 'open'), 0) AS open_work_bytes,
           coalesce(sum(d.bytes) FILTER (WHERE d.kind = 'restore' AND d.state = 'committed'), 0)
               AS committed_restore_marker_bytes
    FROM neosian.record_drafts d, target
    WHERE d.conversation_id = target.id
)
SELECT owners.*,
       (SELECT coalesce(sum(octet_length(convert_to(r.text, 'UTF8'))
                           + coalesce(octet_length(convert_to(r.call_id, 'UTF8')), 0)
                           + coalesce(octet_length(convert_to(r.name, 'UTF8')), 0)
                           + 2 * octet_length(convert_to(r.conversation_id, 'UTF8')) + 256), 0)
        FROM neosian.turn_records r JOIN neosian.record_turns m USING (conversation_id, owner, part)
        WHERE r.conversation_id = target.id) AS published_record_bytes
FROM target CROSS JOIN owners;
```

This snapshot covers only open work, committed restore markers and mapped
record payloads. Adding private record rows again would count them twice.
The parent row, visible headers and skeletons, projections and erasure acts
have separate logical charges. Whole turns, including folded turns, have no
whole-turn rate in the records size rule. A fold reports counts. On extension
2, a folded native write retains its original record count, text byte count
and retry digests. Those fields alone cannot recover charges for tool names
or call IDs repeated across chunks. Keep the settled byte deltas for
historical account usage.

### Closing a conversation

Before removing an account's conversations, stop new writes for that account
and wait for its in-flight writes to finish. Apply the following transaction
to each conversation, including conversations that have only pending work.
In `psql`, set the `conversation_id` variable to the exact ID and enable
`ON_ERROR_STOP`. The example uses the default `neosian` schema.

```sql
BEGIN;
DELETE FROM neosian.record_staging WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.turn_records WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.record_turns WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.record_drafts WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.projections WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.turn_redactions WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.turns WHERE conversation_id = :'conversation_id';
DELETE FROM neosian.conversations WHERE conversation_id = :'conversation_id';
COMMIT;
```

On extension 2, removing each `turns` row also removes its seal header
and digest pages through cascading foreign keys. Confirm that the account
has no rows in either `record_seals` or `record_seal_pages` before reopening
access.

A committed deletion removes the rows from new snapshots. PostgreSQL can
reclaim their space for reuse after older snapshots finish. Backup copies
follow the operator's retention policy.

## Archives and capabilities

`export DIR` writes a portable FileStore archive. `import DIR` preflights all
selected target scopes and conversations for occupancy, then restores their
content, histories, actors and erasure trails. A memory scope with documents,
version history or erasure entries is occupied. A conversation is occupied
when it has turns or projections. If it holds only erasure entries, a restore
keeps those entries and appends the incoming entries in archive order.
Each unit restores separately, so an I/O failure can leave a partial import.
Existing format-1 archives remain readable.

A transfer reads a conversation's erasure trail from a FileStore or
PostgreSQL 500 acts at a time, in the order the source stored them. Every act
arrives in that order, including acts with the same time and acts timed
before the act stored ahead of them. A whole-conversation restore still takes
all of a conversation's acts in one request body. An export from a server
(`--url`) can use only the server's audit read, which answers at most 500
acts, newest first. An answer of fewer acts is the whole trail, written to the
archive oldest first by time, with equal times in stored order. An answer of
500 acts refuses the export in its preflight, because older acts may exist.
Export such a store from the store itself, with `--root` or
`NEOSIAN_POSTGRES_DSN`.

An import into a server that declares `turn_records` version 1
(`import DIR --url`) restores each conversation that holds a turn through
`store/restore_records`, as one restore whose id is `i` and the first 32 hex
digits of the SHA-256 of the conversation id. The preflight probes that
restore and passes over the archive once without sending anything, so a
staged restore that holds something else, or an item too large for a part,
refuses before the first unit is written. The import then reads the archive
one turn at a time and sends the conversation's stream in parts of at most
983,040 bytes and 2,048 items, measured as they are sent; the last part
commits. Nothing of the conversation is visible before the commit. The
send reads the archive again, so before the committing part leaves, the
stream it sent must have the unit digest the preflight measured: an archive
changed between those reads, for example a turn erased while the import
ran, is not committed. The import stops with the staged restore kept, and
the commit's unit digest is checked against the preflight's too. A
conversation without turns goes whole, through `restore_conversation`.

When a request is refused or its answer is lost, the import stops. It
reports a refusal's status, code and `Retry-After`, and it keeps the staged
restore. The server's message is shown with the client's token replaced by
`[credential]` and cut to 512 characters, and a code outside the error
registry's grammar (a lowercase letter, then lowercase letters, digits and
underscores, at most 64 in all), or one that holds the token, is shown as
`unknown`. Run the same command again. A committed restore whose digest is
the archive's counts as done, and one with another digest is refused. An
open restore resumes: the items the server already holds go again first, in
parts of their own, and the server compares each with what it stored, so an
archive changed since the interruption is refused with `restore_mismatch`
before anything is committed. Then the rest follows. A scope, or a
conversation without turns, that an earlier run restored is passed over
only when reading it back shows exactly the archive's unit; otherwise it is
occupied. For a conversation without turns that read covers its erasure
trail, so one that holds only erasure acts is never appended twice; a trail
of 500 acts or more, which the audit read may not show whole, is occupied. A staged restore that the archive no longer matches is removed by
`neosian records drafts --discard` once it is older than
`NEOSIAN_DRAFT_SECONDS`. The import holds one archive turn, one part and
the conversation's projection list in memory. A server without the
capability receives one whole `restore_conversation` per conversation, as
before.

Wire capabilities independently advertise paging, erasure and optimistic
concurrency. Built-in stores support all three and whole-store portability.
A store that keeps records also advertises `turn_records: {version: 1}`, the
five turn-records routes of `neosian docs wire`: a FileStore from the first
handshake after `neosian records enable`, Postgres from the first server
started after the records extension is installed.
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
subset and checks refusals. `profile=records` is the full profile plus a
required `turn_records` version 1: it fails on a service that does not
declare the capability, and on the reference fixtures it enables records
first (`neosian records enable`, or records extension 1 on Postgres). Under
the other two profiles the turn-records cases run when the capability is
declared and are reported as skipped when it is not; a service that does not
declare it owes the five routes nothing. The suite previews
`store/sweep_records` and never sends it `write: true`. `NEOSIAN_TEST_SERVER_PEER_URL` optionally points at
a second worker on the same data to exercise cross-worker races. Without it,
HTTP mode uses one endpoint. The file and Postgres fixtures use two processes.
The runner imports no Python product; Python is test tooling only.

## Current-memory search

FileStore and Postgres advertise optional memory-search version 1, memory
ranking version 1 and memory passages version 1. They share keyword matching over current documents, with
explicit metadata selection and bounded excerpts, and compute relevance scores
in the same Rust matcher, so both answer identically. Files hold the root lock
during a search page; Postgres uses a statement snapshot. No persistent search
index, cache or DDL is added. Custom services declare each capability
separately and pass the optional independent search and ranking cases. See
`neosian docs memory` and `neosian docs wire`.

## History search

FileStore and Postgres advertise optional history ranking version 1 on
`conversation/search_turns`. Files scan turn logs under the store lock and
match with Unicode lowercase. Postgres selects turns with `ILIKE` under
the database collation. Whole legacy turns keep their stored search text.
For a turn stored as records, each term can match a different record's
searchable text or tool-call name in the same turn. A single word split
across record chunks can match on FileStore and miss on Postgres. History
queries retain their usual term syntax across both formats.

Relevance scores the selected turns together. A matching records turn that
exceeds `NEOSIAN_MAX_TURN_BYTES` contributes to the scores and keeps its
rank position. The HTTP answer counts it in `omitted` and leaves its body
and score out of the returned arrays. Size-paged answers count the
omission once and continue from the last ranked position they passed.
The optional trigram indexes described above can also serve the native
record predicates for history search. See `neosian docs memory` and
`neosian docs wire` for the history ranking contract and its conformance
cases for custom services.

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
