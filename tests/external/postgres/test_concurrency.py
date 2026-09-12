"""The store-level half of the N3 done-when: two independent stores (two
pools — the two-web-worker shape) on one database behave correctly under
interleaved writes."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from neosian import Message, PostgresStore, Role
from neosian._foundation.memory.types import MemoryDocument
from neosian._foundation.shared.exceptions import MemoryConflictError
from tests.support.postgres import store_schema

_SCOPE = "user:workers"
_TURNS = 25


@pytest.fixture
async def other(
    postgres_dsn: str, store: PostgresStore
) -> AsyncIterator[PostgresStore]:
    """A second store on the same schema — a second worker process."""
    other = PostgresStore(postgres_dsn, schema=store_schema(store))
    try:
        yield other
    finally:
        await other.aclose()


async def test_turn_numbers_stay_gapless_across_two_workers(
    store: PostgresStore, other: PostgresStore
) -> None:
    """CS3: 25 interleaved appends from two pools yield exactly 1..25."""
    workers = (store, other)
    results = await asyncio.gather(
        *(
            workers[i % 2].append_turn(
                "thread", (Message(role=Role.USER, content=f"m{i}"),)
            )
            for i in range(_TURNS)
        )
    )
    assert sorted(turn.turn for turn in results) == list(range(1, _TURNS + 1))
    read = await store.read_turns("thread")
    assert [turn.turn for turn in read] == list(range(1, _TURNS + 1))
    assert await other.last_turn_number("thread") == _TURNS


async def test_expected_version_race_has_exactly_one_winner(
    store: PostgresStore, other: PostgresStore
) -> None:
    """The reason this substrate exists: `expected_version` arbitrates
    across processes — one writer wins, the other gets the conflict."""
    await store.write(_SCOPE, "doc", "v1")
    results = await asyncio.gather(
        store.write(_SCOPE, "doc", "from-a", expected_version=1),
        other.write(_SCOPE, "doc", "from-b", expected_version=1),
        return_exceptions=True,
    )
    winners = [r for r in results if isinstance(r, MemoryDocument)]
    losers = [r for r in results if isinstance(r, MemoryConflictError)]
    assert len(winners) == 1 and len(losers) == 1
    assert winners[0].version == 2
    assert losers[0].reason == "version_mismatch"
    document = await store.read(_SCOPE, "doc")
    assert document is not None
    assert document.content == winners[0].content


async def test_racing_plain_writes_never_reuse_a_version(
    store: PostgresStore, other: PostgresStore
) -> None:
    await store.write(_SCOPE, "doc", "v1")
    written = await asyncio.gather(
        store.write(_SCOPE, "doc", "racer-a"),
        other.write(_SCOPE, "doc", "racer-b"),
    )
    assert sorted(document.version for document in written) == [2, 3]
    rows = await store.versions(_SCOPE, "doc")
    assert [row.version for row in rows] == [3, 2, 1]
