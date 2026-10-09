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
6. **A format marker.** Every row declares `neosian_format`; a newer
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
| `RemoteStore` behind `neosian serve` | either of the two, over the wire | both and the eraser's slice on both backends, raw-JSON pins beside | `make test`, `make test-container` |
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
