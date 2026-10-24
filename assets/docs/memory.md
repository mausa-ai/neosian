---
title: "Memory and recorded context"
summary: Documents, mounts, version history, deterministic upkeep and the recorded conversation ledger
---

# Memory and recorded context

Memory consists of small markdown documents under scopes. A mount binds a
scope to a virtual directory: `scope=user:me,path=user` exposes `/user`.
Repeat `--mount` for multiple sources. `ro` is read-only; `eo` allows edits
to existing documents while fixing the document set. The store holds no
mount policy; CLI and MCP enforce it before mutations.

The memory tool and CLI share six commands: `view`, `create`, `str_replace`,
`insert`, `delete` and `rename`. View `/` for the index and read a document
before editing it. `str_replace` requires exactly one matching occurrence;
`insert` uses a zero-based line number. A line range on `view` is one-based
and inclusive, with end `-1` meaning the end. A content flag of `-` reads stdin.
Paths have no `.md` suffix at this interface. A cross-scope rename is not atomic.

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

Hooks append numbered turns and log projections. Startup context gives a
bounded index of prior sessions; `recall_turn` rehydrates a named turn and
`search_history` searches recorded text. `continue_session` follows an explicit
handoff. These tools do not run an agent or control its context window.
Conversation search is distinct from document search.

Operator `redact` clears selected turns; `prune` applies an age cutoff to
conversations. Whole-conversation erasure also clears their session documents.
Projections covering erased turns are blanked. Memory and turn formats remain
1; `neosian docs stores` documents the on-disk layout and failure behavior.
