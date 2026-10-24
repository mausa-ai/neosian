---
title: "Stores: author and certify your own"
summary: The two ABCs, the seven constraints, the kits as the standard, a SQLite walk, the table
---

# Stores

neosian ships two substrates and will ship no third: `FileStore`, a
directory, and `PostgresStore`, a schema in your database, each
implementing both storage seams. A host may implement its own store over
anything, and what keeps it honest is not a review but a test run: the
two conformance kits the wheel carries. This page is the contract a
store signs, how to run the kits, what certified means, and a worked
example on SQLite that passes both.

## The contract

Two abstract classes, fifteen methods, no constructor of neosian's:

```python
class MemoryStore(ABC):
    supports_optimistic_concurrency: ClassVar[bool] = False
    async def read(scope, path) -> MemoryDocument | None
    async def write(scope, path, content, *, actor=None, expected_version=None) -> MemoryDocument
    async def delete(scope, path, *, actor=None) -> bool
    async def rename(scope, src, dst, *, actor=None) -> MemoryDocument
    async def list_documents(scope, *, prefix="") -> tuple[MemoryEntry, ...]
    async def versions(scope, path, *, limit=50) -> tuple[MemoryVersion, ...]
    async def redact(scope, *, path=None, actor=None) -> int
    async def history(scope, *, since=None, limit=None) -> tuple[MemoryVersion, ...]
    async def redactions(scope, *, since=None, limit=None) -> tuple[MemoryRedaction, ...]

class ConversationStore(ABC):
    async def append_turn(conversation_id, messages, *, actor=None) -> ConversationTurn
    async def read_turns(conversation_id, *, after=0, limit=None) -> tuple[ConversationTurn, ...]
    async def last_turn_number(conversation_id) -> int
    async def search_turns(query, *, conversations=None, limit=50) -> tuple[ConversationTurn, ...]
    async def append_projections(conversation_id, entries) -> None
    async def read_projections(conversation_id, *, after=0, limit=None) -> tuple[ConversationProjection, ...]
```

Seven constraints keep the memory seam implementable over anything:

1. **Async signatures, no I/O ownership.** A store never connects,
   commits, migrates or issues DDL inside a method; every method is
   safe inside a caller-owned transaction and assumes no durability on
   return. Schema application is an explicit act of the host.
2. **Read-your-writes within a task.** The memory tool checks, then
   writes; without this an agent duplicates documents.
3. **Scope-wide `redact`.** `path=None` is the whole scope. Content
   clears on the live document and on every version row; paths,
   versions, timestamps and actors stay; nothing is deleted;
   `updated_at` does not move. The return is the count of distinct paths
   matched, the same on a second call.
4. **Timestamps tz-aware UTC**, from an injectable `Clock`, never the
   database's own `now()`. A naive datetime in stored data is refused,
   never coerced.
5. **Versions per document, monotonic from 1, full content on every
   row.** A delete consumes the next number, a re-create continues from
   the highest ever issued, an identical write still bumps, a rename is
   `deleted` at the source and `created` at the destination (which
   continues its own history).
6. **A format marker.** Memory envelopes and version rows declare
   `neosian_format`; a newer
   value is refused (`memory_format_unsupported`), and unknown keys a
   substrate holds beside the contract's fields come back in `extra` and
   survive a write.
7. **No policy.** Mounts, read-only and edit-only enforcement, the index,
   the prompt pack: the tool layer's. A store validates scope then path
   with the shipped parsers and interprets neither.

The conversation seam is smaller: append-only turns numbered per
conversation from 1 without gaps, messages stored through the public
codec (`message_to_json`, `message_from_json`) and returned verbatim,
projections in `(turn, span, insertion)` order, the same format marker,
and no interpretation: the store never checks that a projected turn
exists, never trims, never invents an id. Turns are never deleted. The
one way content leaves is the eraser beside the ABC (below): a redacted
turn keeps its number, `created_at` and `actor`, carries no messages and
reads `redacted=True`, the one additive field N8 put on `ConversationTurn`,
and every reader of a turn accepts it.

`search_turns` is one rule on every substrate, and the kit pins it: the
query splits on whitespace into lowercased terms; a turn matches when
every term is a case-insensitive substring of its searchable text, which
is `turn_text` from `neosian.conversation` (each message's text, each
tool call as its name and compact JSON arguments, tool results, joined
by newlines, no role labels, so "user" matches nothing); hits come
newest first under one total order, `created_at`, then
`conversation_id` in codepoint order, then `turn`, each descending; the
answer holds at most `limit` turns; `conversations=None` is the whole
store, a sequence narrows, `()` answers `()`; a blank query or `limit`
below 1 is a `ValueError`. ASCII folding is pinned; beyond ASCII a
substrate folds as its engine does. A store may render the text at read
(FileStore scans its turn log) or keep it in a column written at append
(PostgresStore's schema generation 3, the SQLite example); a row written
before such a column holds nothing and never matches until an export
and import re-renders it. `parse_query` and `match_terms` are public for
a store that scans. Over the method sit the `search_history` tool
(`neosian docs memory`, `neosian docs mcp`) and the `neosian search`
verb (`neosian docs cli`).

Two names are reserved and must not be claimed: `MemoryStore.search`
and `ConversationStore.list_conversations`, each a 1.x additive that
arrives only by a recorded decision; a store may implement either
early (`search_turns` arrived by exactly that route at N5). Three
protocols beside the ABCs are optional and cost a host
nothing: `Pageable`, four `*_page` listings behind an opaque cursor,
which is what `neosian serve` requires of a store it opens; `Portable`,
which `neosian export` and `import` need (`neosian docs memory`); and
`Erasable` (DESIGN §38), the turn eraser `neosian redact` and `prune`
need. `redact_turns(conversation_id, *, through=None, turns=None,
actor=None)` blanks the selected turns of one conversation and every
projection entry covering them, keeps each turn's number, `created_at`
and `actor`, and answers the count matched, already-redacted turns
counted, so a repeat changes nothing; both selectors absent is every
turn, `through=N` turns 1 to N, `turns` the numbers named, both given or
a number below 1 a `ValueError`, an unknown conversation or number
matching nothing. `turn_redactions(*, conversations=None, since=None,
limit=50)` reads the trail, `ConversationRedaction(conversation_id,
turns, actor, created_at)` rows newest first under `created_at`, then
the id in codepoint order, then the act's position, bounded like a
search. A redacted turn never answers a search again (a stored
`search_text` empties with it), and a reader older than 1.8.0 refuses
its row as an unsupported format. The three shipped stores implement all
three protocols; the state process transmits `erasable` in its handshake
(`neosian docs wire`). Everything a store imports is public: the value
types, the format constants, `parse_scope`, `validate_document_path`,
`parse_conversation_id`, the codec, the error classes and `Clock`, from
`neosian.memory` and `neosian.conversation`.

## The kits

The kits are two pytest base classes in the wheel; neosian never
imports them at runtime, and they need pytest with pytest-asyncio.
Subclass them, provide a `store` fixture, inherit the tests:

```python
import pytest

from neosian.conversation.testing import ConversationStoreContract
from neosian.memory.testing import MemoryStoreContract

from my_store import MyStore


@pytest.fixture
def store(tmp_path):                      # function-scoped, empty, isolated
    return MyStore(tmp_path / "state.db")


class TestMyStoreMemory(MemoryStoreContract):
    async def plant_raw_document(self, store, scope, path, *, content, format_version, extra):
        ...                               # a raw row, past the store


class TestMyStoreConversations(ConversationStoreContract):
    async def plant_raw_turn(self, store, conversation_id, *, line):
        ...
    async def plant_raw_projection(self, store, conversation_id, *, line):
        ...
```

`MemoryStoreContract` carries 42 tests (68 items once parametrised over
seven round-trip payloads, ten bad scopes and twelve bad paths), the
ledger reads and the concurrency contract included; `ConversationStoreContract`
carries 44 (56 items), the fifteen of its search slice
(`SearchContract`, also exported alone: run it a second time under a
clock that never advances and the total order's tiebreak becomes a real
check). `ErasureContract`, twelve tests, is exported beside them and
never inherited: a store that implements `Erasable` subclasses it too,
with the same `store` fixture. The `store` fixture must start empty on every
test, and `test_store_starts_empty` fails loudly when it leaks; the
`scope` and `conversation_id` fixtures are overridable for a substrate
that is not thrown away between tests. The planting hooks write one raw
row past the store so the kits can check that a newer format or a
malformed row is refused and that unknown keys survive; a kit without
them skips those six tests, so implement them. Inject a `Clock` whose
reads advance: ordering tests then never depend on wall time, and a
failure reads as a sequence rather than a race.

## Certified

A store is certified when both kits are green in its own CI against a
named neosian version, with the planting hooks implemented so that
nothing skips (`pytest -rs` lists none), on the substrate the store
claims. Say so as "passes the neosian store kits at <version>", and pin
that version: the kits gain tests when the contract gains a ruling,
additive at a minor from v1.0.0, never silently.

Custody stays where the substrate is. neosian ships the two reference
stores and the kits, and no third store: a store's correctness lives
where substrates differ (codepoint ordering, what a text column refuses,
whose clock stamps a row), and a store maintained by nobody would be a
compliance bug inside the contract. Community stores in community
hands, the contract ours.

## The worked example: SQLite

`examples/sqlite_store.py` is a third substrate that passes both kits in
neosian's own unit tier (`uv run pytest tests/unit/examples/test_sqlite_store.py`,
146 passed, none skipped) and is never shipped in the wheel: SQLite
stays community custody, so copy the file, it imports the two public
facades and nothing else.

One connection on one file. Every mutation is a `BEGIN IMMEDIATE`
transaction, every read one statement, all of it inline on the event
loop under one `asyncio.Lock`. The schema is the Postgres one in SQLite
terms, applied idempotently at construction. Timestamps are fixed-width
ISO-8601 `Z` text so text order is time order (a stamp without its
microseconds would sort after one with them), flags are integers, and
`extra` and `messages` are JSON text. Listings use `substr` rather than
`LIKE`, which is case-insensitive and needs escaping, and order `COLLATE
BINARY`, the codepoint order the reference stores pin. Search matches
the `search_text` column the append renders with `turn_text`, one
`instr(lower(...))` per term (stock SQLite's `lower()` folds ASCII only,
which is what the kit pins), and a file from before the column gains it
on open through a `PRAGMA table_info` guard, the converge-by-ALTER story
the Postgres asset tells.

`supports_optimistic_concurrency` is True for a reason of SQLite's own:
`BEGIN IMMEDIATE` takes the file's single writer lock across processes,
so a second writer's `expected_version` check reads the committed
version and fails, and the `(scope, path, version)` primary key is the
second guard; two instances on one file arbitrate, and the test says
so. What differs from Postgres is recorded beside it: a U+0000 in
content round-trips where Postgres refuses it, and there is no parent
`conversations` table because nothing in the contract needs one. Not
for a network filesystem, where SQLite's locking is unreliable.

## Certified stores

| Store | Substrate | Kits | Run by |
|---|---|---|---|
| `FileStore` | a directory of markdown and JSON lines | both, and the eraser's slice | `make test`, every commit |
| `PostgresStore` | a schema in Postgres | both, the eraser's slice, and the cross-worker race | `make test-postgres`, CI |
| `RemoteStore` behind `neosian serve` | FileStore and Postgres from either server | both and the eraser's slice; raw-JSON pins beside | `make test`, `make test-postgres`, `make test-differential`, `make test-container` |
| `examples/sqlite_store.py` | one SQLite file | both (not `Erasable`) | `make test`, every commit |

A community store joins the table by pull request naming its
repository, the neosian version its run pinned, and the green run.

## Durable messages and reminders

See `neosian docs messaging` for the shared `messages` tool, conversation and
scope destinations, history annotations, explicit acknowledgment and claims.
Reminders can be snoozed with a finding and a new due time on the same ID.
Native conversations opt in with `mailbox=MailboxConfig()` from
`neosian.messaging`. Regenerate client adapters after upgrading for automatic
delivery. Pi and OpenCode also support wake requests while attached.

## Stored formats and interchange

The source tree and wheel carry `neosian/assets/contracts/state-v1.schema.json`.
It is a definitions bundle: select the named `$defs` entry for the record
being validated, rather than validating against the unrestricted root.
`wire-v6.openapi.json` beside it references the same definitions for HTTP
responses. Neither file changes the existing format or wire version.

| Record | Version and representation | Schema definition |
|---|---|---|
| Live memory | `neosian_format: 1` YAML frontmatter followed by the exact Markdown body | `MemoryEnvelope` for the header; `Document` for the flattened wire record |
| Memory versions | format 1 JSON lines, including full content and deleted/redacted skeletons | `VersionRow`; `Version` on the wire |
| Memory erasure trail | legacy JSON lines with `ts`, actor, path and count; no format marker | `MemoryRedactionRow`; `MemoryRedaction` uses `created_at` on the wire |
| Conversation turns | format 1 JSON lines; the directory supplies the conversation ID | `TurnRow`; `Turn` includes the ID on the wire |
| Projections and turn erasures | format 1 JSON lines; projection span covers consecutive turns ending at `turn` | `ProjectionRow`, `TurnRedactionRow`; their wire definitions omit the marker |
| Mailbox | JSON body with its own `format: 1`, inside an ordinary memory document | `Mailbox` |

FileStore paths remain percent-encoded scope directories with `documents/`,
`versions/` and `redactions.jsonl`; conversations live under
`conversations/<id>/`. The Postgres schema generation is 4, separate from
these record versions. All writers must preserve monotonic document and
turn numbers, actors, timestamps and erasure skeletons. A memory redaction
does not advance the document version or `updated_at`, so neither is an
erasure cursor.

Canonical timestamps are UTC with `Z`. A message keeps its text, image,
document or compaction blocks, tool calls, reasoning and opaque `extra`
fields. Recorded provider metadata is data: reading or transferring it
does not require that provider's client, account or model. Newer memory
and turn format markers are refused, never silently rewritten.

Unknown memory metadata that is JSON-compatible can travel in `extra`.
Rust preserves unknown frontmatter lines on local writes, but its wire
and archive conversion supports scalar and inline JSON values, not every
handwritten block-YAML value. The independent fixtures exercise nested
inline JSON. They do not establish lossless conversion of arbitrary YAML;
that compatibility gap remains to be resolved before the major-version cut.

`tests/contract/fixtures/v1/` holds literal homes and equivalent wire
records. `make test-contract` reads them through the Rust binary without
importing the Python product; the existing kits remain the broader store
certification checks during the transition.

## Independent HTTP conformance

From a source checkout, `make test-storage-contract` runs the independent
wire suite against two Rust processes sharing a temporary FileStore home.
`backend=postgres` uses `NEOSIAN_TEST_POSTGRES_DSN`, applies the shipped SQL
to one new schema, runs two processes and drops only that schema afterward.
The server never applies DDL. Both paths check competing versioned writes,
concurrent unconditional writes and gapless concurrent turn appends.

For an independently operated storage service, run
`make test-storage-contract backend=http` with `NEOSIAN_TEST_SERVER_URL`
and `NEOSIAN_TEST_SERVER_TOKEN`. Use a disposable test deployment and an
unconstrained token: cases write, delete, redact and restore under fresh
random test scopes and conversations. External history is left for the
test deployment's owner to dispose of. An optional
`NEOSIAN_TEST_SERVER_PEER_URL` exercises a second worker on the same store;
without it, concurrency is tested through one endpoint only.

The default `profile=full` requires optimistic concurrency, paging,
turn erasure and whole-store portability. `profile=base` verifies the
required wire, runs cases for declared optional capabilities, checks
refusals for missing ones and reports their skipped cases. A missing
capability cannot produce a full-profile pass. A false concurrency flag
does not permit ignoring a serial `expected_version` mismatch.

Report the revision, backend/deployment, profile, passed/skipped counts
and whether separate workers were exercised. These checks cover the wire
behaviors named in `tests/contract/storage/README.md`; the existing
substrate corruption, transport and operator suites remain until their
independent replacements are complete. The HTTP runner itself needs no
installed neosian Python package, database driver or local binary.
