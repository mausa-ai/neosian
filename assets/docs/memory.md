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

## Deterministic maintenance

`memory maintain --dry-run` lists aged empty documents and exact duplicates.
Without `--dry-run`, it deletes those candidates with the configured actor.
The default `--min-age-days 7` protects recent updates. Redacted documents,
read-only and edit-only mounts, messages and message links are excluded.
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
