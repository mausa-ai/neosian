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

Two abstract classes, fourteen methods, no constructor of neosian's:

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
exists, never trims, never invents an id.

Two names are reserved and must not be claimed: `MemoryStore.search`
and `ConversationStore.list_conversations`, each a 1.x additive that
arrives only by a recorded decision; a store may implement either
early. Two protocols beside the ABCs are optional and cost a host
nothing: `Pageable`, four `*_page` listings behind an opaque cursor,
which is what `neosian serve` requires of a store it opens; and
`Portable`, which `neosian export` and `import` need (`neosian docs
memory`). Everything a store imports is public: the value types, the
format constants, `parse_scope`, `validate_document_path`,
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
carries 29 (41 items). The `store` fixture must start empty on every
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
115 passed, none skipped) and is never shipped in the wheel: SQLite
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
BINARY`, the codepoint order the reference stores pin.

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
| `FileStore` | a directory of markdown and JSON lines | both | `make test`, every commit |
| `PostgresStore` | a schema in Postgres | both, and the cross-worker race | `make test-postgres`, CI |
| `RemoteStore` behind `neosian serve` | either of the two, over the wire | both on both backends, raw-JSON pins beside | `make test`, `make test-container` |
| `examples/sqlite_store.py` | one SQLite file | both | `make test`, every commit |

A community store joins the table by pull request naming its
repository, the neosian version its run pinned, and the green run.
