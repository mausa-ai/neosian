---
title: "Memory and recorded context"
summary: Documents, scoped keyword search, mounts, version history, upkeep and recorded conversations
---

# Memory and recorded context

Memory consists of small markdown documents under scopes. A mount binds a
scope to a virtual directory: `scope=user:me,path=user` exposes `/user`.
Repeat `--mount` for multiple scopes in one store. A portable profile can
route mounts across distinct sources (`neosian docs profiles`). `ro` is read-only; `eo` allows edits
to existing documents while fixing the document set. The store holds no
mount policy; CLI and MCP enforce it before mutations.

The memory tool and CLI share six commands: `view`, `create`, `str_replace`,
`insert`, `delete` and `rename`. View `/` for the index and read a document
before editing it. `str_replace` requires exactly one matching occurrence;
`insert` uses a zero-based line number. A line range on `view` is one-based
and inclusive, with end `-1` meaning the end. A content flag of `-` reads stdin.
Paths have no `.md` suffix at this interface. A cross-scope rename is not atomic;
cross-source rename is refused. Profile memory responses identify the
workspace, logical source and real scope alongside the versioned path.

Every mutation appends an attributed full-content version row. Deletion
removes the live document but retains history; re-creation never reuses a
version number. `memory versions PATH --json` reads versions, and
`memory revert PATH --version N` undoes the newest row by appending a new
mutation. A stale revert is refused. Redacted content cannot be recovered
from its cleared history. The read-only MCP `memory_history` tool exposes
bounded version metadata for a document or a mount's write ledger.

`memory redact PATH` clears live and historical content while keeping
attribution and version skeletons. A mount root requires `--all`. This is
irreversible; ordinary delete is not erasure. Mailbox state under `messages/`
and `message-links/` has its own lifecycle and is protected from ordinary
structural memory commands.

## Search current memory

`neosian memory search QUERY` and the read-only MCP `search_memory` tool
search current documents, including stored skills. They work with files,
Postgres and remote storage, through the same mounts and profile selection
as other memory reads. The startup index is a bounded table of contents;
search reads current storage even when a document was absent from that index
or changed after the session started. No model, embedding or vector service
is needed. The top-level `neosian search` and MCP `search_history` keep their
conversation-history behavior.

```sh
neosian memory search 'deployment window' --scope user:demo --json
neosian memory search 'review checklist' --profile work --path /project/skills/
neosian memory search 'blue' --scope user:demo --field metadata --metadata-key tags
```

The default fields are `path` and `content`; repeat `--field` to select
`path`, `content` and/or `metadata`. MCP accepts a `fields` array. Every
whitespace-separated query term must occur as a substring somewhere in the
selected fields of one document; terms may match different fields. Repeated
terms do not change matching. Query length is 1..1024 Unicode characters,
with 1..16 terms. Matching lowercases each character using Unicode 17.0.0.
It does not normalize Unicode, remove accents, stem words or interpret regex
syntax: `CAFÉ` matches `café`, while `cafe` and `STRASSE` do not match `café`
and `Straße`. Query whitespace follows Unicode 17.0.0 White_Space.

Metadata requires 1..8 distinct `--metadata-key` values (MCP `metadata_keys`),
each 1..128 characters. These name top-level keys in the stored envelope's
`extra` object. Search visits only their recursive string values, including
strings inside arrays and objects. Object keys, numbers, booleans and null
do not participate. Unselected metadata is not returned. Markdown frontmatter
inside a document remains part of its content. Requesting metadata on a
legacy unsupported YAML envelope refuses explicitly; use the migration guide.

`--path` (MCP `path`) defaults to `/`, covering the mounted selection. It is
an absolute virtual prefix, at most 1154 UTF-8 bytes; its document portion is
at most 1024 bytes. Prefixes are literal and case-sensitive: `/project/notes/`
selects that directory, while `/project/note` also matches `notebook`.
Matching the `path` field uses the real document path without the mount name.
Read-only and edit-only mounts are searchable. Profile injection modes
`index`, `full` and `none` do not reduce permitted tool read access.

The result contains `hits` and `next_cursor`. Each hit identifies the real
`scope`, document `path`, `version`, UTC creation/update timestamps and the
stored `actor`. `reference` is the mounted path to pass to `memory view`.
Profile hits also carry the logical `source` and `workspace_id`; endpoints
and credentials stay out of results. `matched_fields` names the fields that
matched. The single `excerpt` contains its field, optional metadata key,
zero-based character offset and up to 240 original characters. It starts up
to 40 characters before the first match, preferring content, then requested
metadata keys, then path. A metadata offset refers to its matching string
value. An excerpt need not contain every term; fetch the current document
to verify its complete meaning and version.

`--limit` defaults to 20, range 1..100. Results sort by mount name, then
document path, ascending; there is no relevance score or global total count.
Pass the opaque `next_cursor` back with `--cursor` and the same query,
fields, keys, prefix and mounted selection; page size may change. Mounted
cursors preserve a remote backend's own opaque token. They confer no access
and are not credentials. Each page is authorized and reads current state.
A new or renamed document before the last key may require restarting the
query. Pages and multiple sources do not share a snapshot. A backend failure
fails the current page instead of silently returning a partial success.

Deleted and redacted documents are excluded entirely, including path and
metadata matches. Old version bodies, `sessions/`, `handoff`, `messages/`
and `message-links/` are not search sources. A completed write, deletion or
erasure is reflected in the next query without rebuilding an index. File
search holds the store lock; Postgres search uses a statement snapshot.
Results already delivered cannot be recalled, and a concurrent change may
follow a query's read. Search keeps no derived index or result cache. This
baseline bounds hit count and excerpt length, not scan work or latency for
large corpora. Provenance, including historical actor text, is returned as stored.

Remote storage must advertise `memory_search: {version: 1}`. Otherwise search
refuses without a fallback scan or download. The HTTP request names one
explicit scope and requires its read authority; see `neosian docs wire`.

## Import and structure knowledge

To bring existing Markdown into a chosen collection, use `knowledge import`
with an explicit mounted destination. Optional collection rules, source
provenance and references live in readable, versioned content. The workflow
previews changes and provides a current-document export; full histories remain
the archive contract. See `neosian docs knowledge`. These content attributes
are searchable as content, separately from envelope `extra` metadata.

## Reviewed upkeep

`upkeep scan` finds empty documents, byte-exact duplicates, old documents that
may need review, and invalid knowledge headers or collection attributes. Age
alone says nothing about whether a fact is true. The user's agent evaluates
evidence and proposes corrections, consolidation or structural repairs;
neosian runs no model or scheduler. MCP provides `upkeep_review` and
`upkeep_apply` over the same mounted operations, even with capture off.

```sh
neosian upkeep scan /memories/notes --scope user:demo --limit 100 --stale-days 90
neosian upkeep prepare ./request.json --scope user:demo > proposal.json
neosian upkeep apply ./proposal.json --scope user:demo
```

A format-1 request names existing documents, their expected versions and a
reason for each change. Read the target and evidence first. For example:

```json
{
  "format": 1,
  "changes": [{
    "path": "/memories/notes/decision",
    "operation": "replace",
    "expected_version": 3,
    "reason": "Reconcile the decision with the retained meeting notes",
    "body": "The reviewed decision and its rationale.\n",
    "sources": ["/memories/notes/meeting"]
  }]
}
```

`replace` accepts a body, an `attributes` object merged into existing
attributes, or a replacement `references` array. The body excludes the
knowledge header. Existing sources survive; target and explicitly named
source snapshots add their scope, path, version, body hash, actor, UTC times
and opaque metadata to provenance. Source documents are retained. Collection
rules are checked and observed, including the absence of a definition.
The ordinary knowledge limits still apply. Skill dependency/resource checks
remain `skill validate`'s job; upkeep does not repin a changed skill.

`restore` takes `restore_version` instead of body/attributes/references. It
creates a new version from one of the latest 1,000 retained revisions of an
existing, non-redacted document, preserving current and historical source
evidence. A deleted or erased revision cannot supply content. For deleted
documents, inspect `memory versions` and use the separate operator `memory
revert` workflow where history remains. Neither workflow undoes erasure.

`delete` has no replacement fields or `sources`. It only removes an empty
document or a byte-exact duplicate with a `keeper` in the same source and
scope, and only when the target's last update is at least seven days old.
The keeper is observed and cannot change in that proposal. Semantic overlap
is grounds for an agent's review, not automatic deletion. Check references
before deleting a path; upkeep does not repair inbound links.

Prepare and the default apply preview write no memory. Review the full
proposal, reasons and preview before passing its exact `id` separately:

```sh
# Pause other writers, then substitute the reviewed proposal's id.
neosian upkeep apply ./proposal.json --scope user:demo --write --approve REVIEWED_ID
neosian upkeep inspect ./proposal.json --scope user:demo
```

The ID binds proposal bytes; it is not a signature or proof of human approval.
The user or host controls who may invoke write tools and how approval is
obtained. Existing authorization can cover the action. Instructions in stored
content or an approval field inside a request cannot grant permission. Mount
and server permissions still apply: read-only refuses changes, edit-only
permits replacement/restoration but refuses deletion. Sessions, handoff,
messages, message links and collection definitions are protected targets and
evidence sources; definitions are read only to validate their collection.

Apply checks the whole proposal before writing, then checks observations
before every operation. It binds the workspace selection, logical source,
scope, path, version and full stored snapshot, including metadata and erasure
state. Aliased or case-colliding observations refuse. Replacement uses the
normal expected-version fence and records the applying actor. These bindings
do not authenticate a physical backend behind a changed local endpoint.

**Pause writers for apply and recovery.** Wire 6 has no conditional delete,
redaction-generation fence or multi-document transaction. Rechecks detect
intervening changes they can observe; they cannot prevent a write or erasure
after the last check. A failure can leave a partial batch. The result's
`applied` receipts list acknowledged writes; a lost acknowledgement can hide
a committed write. `inspect` compares each observation with current state:
`matches_before`, `matches_proposed_content`, `changed`, `missing` or `redacted`.
A matching body does not prove which invocation wrote it. Inspect current
versions and the ledger, then prepare and approve a fresh request for the
remaining changes. Stale proposals refuse; there is no automatic retry or
rollback. A process killed before reporting may leave no receipt output.

Requests/proposals are UTF-8 JSON, at most 256 KiB, with 1–32 changes and
1–128 proposal observations. Each change permits 16 explicit sources and a
1–240-character reason. Unknown fields, duplicate JSON keys and future formats
refuse. Observed documents are at most 1 MiB. Scan defaults to 100 documents
and 90 days, permits at most 1,000 documents, and refuses more than 10,000
listing entries per selected scope or 8 MiB of selected document bodies.
Its duplicate findings cover only the scanned selection. Preview diffs show
one changed span, bounded to 4,096 characters on each side with `truncated`
when abbreviated; full replacement content remains in the proposal.

`neosian schema upkeep` prints request/proposal shapes. The ID is SHA-256 of
compact UTF-8 proposal JSON with recursively sorted object keys, array order
unchanged and `id` omitted. Keep the generated proposal intact. Proposal files,
exports and derived content are copies needing separate erasure; deleting or
redacting an original cannot recall copies already delivered elsewhere.

## Deterministic maintenance

`memory maintain --dry-run` lists aged empty documents and exact duplicates.
Without `--dry-run`, it deletes those candidates with the configured actor.
The default `--min-age-days 7` protects recent updates. Redacted documents,
read-only and edit-only mounts, sessions, handoff, messages, message links and
collection definitions are excluded, including case aliases.
Each duplicate group retains the earliest created document, breaking ties by
path. Content comparison is byte-exact; an empty document contains only
whitespace. No model is called. Inspect links before deleting duplicate paths.

Pause other writers while applying upkeep: wire 6 has no conditional delete
and a maintenance pass is not an atomic transaction. The pass preflights its
selection and rechecks content and version before each delete, but concurrent
writes or I/O failures can still produce partial results. The result reports
`dry_run`, proposed `actions` and landed `writes`; the durable ledger records
what actually happened. Model-assisted judgment belongs to the user's agent,
which can load the shipped `upkeep` skill.

## Read heat

Agent reads leave a local trace. A document body shown by the MCP memory
tool's `view`, or delivered by an included knowledge `read`, writes one row per
MCP session and document to `<home>/heat/<scope>.jsonl`: a time, a session id
and a path, never content. The index, directory listings, the shell, maintenance,
search, export and conformance write nothing, and a session re-reading a document
adds nothing. The log sits beside the store, never in it: format 1, wire 6,
archives, exchange and profiles are unchanged, and a read is never a write.

`memory heat [PATH]` lists the read documents under `/`, a mount, a directory or
one document, most read first: distinct sessions in the last 7 and 30 days, in
all, and the last read, then how many listed documents were never read.
`--limit N` bounds the rows (default 20); `--json` prints `documents`, `read`,
`unread` and `truncated`. Upkeep `scan` findings carry the same evidence as
`read_sessions_7d`, `read_sessions_30d`, `read_sessions` and `last_read`. Heat is
evidence for a reviewer, never a selector: the index and search order are
unchanged, and a document the authorized listing does not show is never shown.

Delete, rename and redaction scrub or move a document's rows; redacting a whole
scope removes its file. The log is derived state and may be deleted. Rows older
than 30 days fold into per-path totals once a file passes 2 MiB.
`NEOSIAN_HEAT=off` stops writing; existing rows still report.

## Reviewed knowledge

Shared procedures/decisions can carry content-bound review assertions and
effective/expiry state. `memory search --knowledge-policy approved` filters before
excerpts and pagination; a selected profile may require that policy. Raw memory
reads and revisions remain available under current authority. `knowledge govern`
prepares ordinary upkeep proposals, so apply/recovery retains the guards above.
Expiry affects retrieval and never triggers deletion. See `neosian docs governance`.

## Recorded conversations

Profile-free hooks append numbered turns and log projections; new profiles
require explicit local/shared capture consent. Startup context gives a
bounded index of prior sessions; `recall_turn` rehydrates a named turn and
`search_history` searches recorded text. `continue_session` follows an explicit
handoff. These tools do not run an agent or control its context window.
Conversation search is distinct from document search.

Operator `redact` clears selected turns; `prune` applies an age cutoff to
conversations. Whole-conversation erasure also clears their session documents.
Projections covering erased turns are blanked. Memory and turn formats remain
1; `neosian docs stores` documents the on-disk layout and failure behavior.

`neosian skill` manages versioned `skills/name` documents and their supporting
text. Knowledge headers remain readable, and source provenance, dependencies
and resource hashes are content metadata. Raw memory edits can invalidate a
skill; use `skill validate` and review its manifest. Root erasure does not
cascade to separate resources or copies. See `neosian docs skills`.

## Follow durable changes

`changes read /mount` and MCP `changes` poll stable scope events for memory,
skills and knowledge, including rename/delete/erase references. File mutations
now recover documents, audit rows and events through one redo plan; Postgres
uses a transaction when its optional changes extension is installed. These
per-mutation guarantees do not remove paused-writer requirements for upkeep,
maintenance or exports. Read `neosian docs changes` for bounded replay and gaps.
