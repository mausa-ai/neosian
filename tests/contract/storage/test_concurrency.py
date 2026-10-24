"""Independent HTTP requests compete; local backends use two real server processes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tests.contract.storage.support import Storage, message, refused
from tests.contract.support import Reply


def test_only_one_competing_versioned_writer_wins(storage: Storage) -> None:
    storage.require("supports_optimistic_concurrency")
    storage.memory("write", path="doc", content="original")
    barrier = Barrier(2)

    def write(number: int) -> Reply:
        server = [storage.server, storage.peer][number]
        barrier.wait(timeout=10)
        return server.request(
            "/v1/memory/write",
            {
                "scope": storage.scope,
                "path": "doc",
                "content": f"writer-{number}",
                "expected_version": 1,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        replies = list(workers.map(write, range(2)))
    assert sorted(reply.status for reply in replies) == [200, 400]
    loser = next(reply for reply in replies if reply.status == 400)
    assert refused(loser, "memory_conflict")["reason"] == "version_mismatch"
    winner = next(reply.body["document"] for reply in replies if reply.status == 200)
    assert winner["version"] == 2
    assert storage.memory("read", path="doc")["document"] == winner
    assert [
        r["version"] for r in storage.memory("versions", path="doc")["versions"]
    ] == [2, 1]


def test_concurrent_appends_never_reuse_or_lose_a_turn(storage: Storage) -> None:
    storage.require("supports_optimistic_concurrency")
    barrier = Barrier(8)

    def append(number: int) -> Reply:
        server = [storage.server, storage.peer][number % 2]
        barrier.wait(timeout=10)
        return server.request(
            "/v1/conversation/append_turn",
            {
                "conversation_id": storage.conversation,
                "messages": [message(f"input-{number}")],
            },
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        replies = list(workers.map(append, range(8)))
    assert all(reply.status == 200 for reply in replies), replies
    assert sorted(reply.body["turn"]["turn"] for reply in replies) == list(range(1, 9))
    turns = storage.turns("read_turns")["turns"]
    assert [turn["turn"] for turn in turns] == list(range(1, 9))
    assert {turn["messages"][0]["content"] for turn in turns} == {
        f"input-{n}" for n in range(8)
    }
    assert storage.turns("last_turn_number") == {"turn": 8}


def test_unconditional_writes_keep_every_version(storage: Storage) -> None:
    storage.require("supports_optimistic_concurrency")
    barrier = Barrier(6)

    def write(number: int) -> Reply:
        server = [storage.server, storage.peer][number % 2]
        barrier.wait(timeout=10)
        return server.request(
            "/v1/memory/write",
            {"scope": storage.scope, "path": "doc", "content": f"input-{number}"},
        )

    with ThreadPoolExecutor(max_workers=6) as workers:
        replies = list(workers.map(write, range(6)))
    assert all(reply.status == 200 for reply in replies), replies
    assert sorted(reply.body["document"]["version"] for reply in replies) == list(
        range(1, 7)
    )
    rows = storage.memory("versions", path="doc")["versions"]
    assert [row["version"] for row in rows] == [6, 5, 4, 3, 2, 1]
    assert {row["content"] for row in rows} == {f"input-{n}" for n in range(6)}
    assert (
        storage.memory("read", path="doc")["document"]["content"] == rows[0]["content"]
    )
