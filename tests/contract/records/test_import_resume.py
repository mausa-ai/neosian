"""An interrupted `neosian import DIR --url` resumes (the T1e2e slice of the
T1e2 plan, DESIGN §40.54 items 12 and 22): the import is cut before its
first part arrives, after an acknowledged header, inside a turn's records,
after a projection batch, after an act batch, and after the server
committed but its answer was lost. Each time the same command is run
again. It probes, repeats in parts of their own exactly the items the
server holds, then sends the rest, and the server commits the unit digest
computed here from the archive's rows, which is the digest an
uninterrupted import commits. Until the commit nothing of the conversation
is visible; after it, one copy. An archive changed after the interruption
is refused before the commit, a committed restore counts only with the
archive's digest, and a staged restore that is not a place in the
archive's stream is refused before anything is written."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.stub import Exchange, Rule, Stub, forwarding
from tests.contract.records.importing import (
    Conversation,
    Turn,
    answered_position,
    failure,
    is_probe,
    items,
    message,
    restore_id,
    restores,
    rich,
    run,
    units,
    within_bounds,
)
from tests.contract.records.restoring import RESTORE, header, placed, status
from tests.contract.records.support import record
from tests.contract.records.test_restore_reads import ABSENT, seen
from tests.contract.support import Binary, Server, serve

# The unit digest of `rich()`, computed from its rows by `importing.py`
# and committed by the uninterrupted import of `test_import.py`.
RICH = "896f8db96c12b00da81bd7304359079f0920aa8a92a59052d7f4018af33e523b"

# Where each stage stops the first run: which content part (1-based) is
# lost, as a request that never arrives (`cut`) or an answer lost after
# the server applied it (`drop`), and the kind of the last item the server
# then holds.
STAGES: dict[str, tuple[str, int, str | None]] = {
    "before opening": ("cut", 1, None),
    "after an acknowledged header": ("cut", 2, "header"),
    "inside a turn's records": ("drop", 2, "record"),
    "after a projection batch": ("drop", 3, "projection"),
    "after an act batch": ("drop", 5, "act"),
    "after the commit": ("drop", 6, "act"),
}


def kind(item: dict[str, Any]) -> str:
    if "redacted" in item:
        return "header"
    if "seq" in item:
        return "record"
    return "projection" if "span" in item else "act"


def content(log: list[Exchange], run_number: int) -> list[Exchange]:
    return [e for e in restores(log) if e.run == run_number and not is_probe(e)]


def lose(stub: Stub, how: str, part: int) -> None:
    """Loses content part `part` of the first run; its probe is request 1."""
    stub.add(
        Rule(
            when=lambda e: e.route == RESTORE and e.run == 0 and e.nth == part + 1,
            kind=how,
        )
    )


def archive_of(binary: Binary, conversation: Conversation) -> Path:
    root = binary.directory / "archive"
    conversation.write(root)
    return root


def published_once(server: Server, binary: Binary, conversation: Conversation) -> None:
    """The committed conversation reads as the archive, once."""
    listing = server.post("store/conversations")["conversations"]
    assert listing == [conversation.id]
    read = server.post("conversation/read_turns", conversation_id=conversation.id)
    assert read["turns"] == [turn.read(conversation.id) for turn in conversation.turns]
    entries = server.post(
        "conversation/read_projections", conversation_id=conversation.id
    )
    assert entries["entries"] == conversation.ordered_projections()
    trail = binary.home / "conversations" / conversation.id / "redactions.jsonl"
    rows = [json.loads(line) for line in trail.read_text("utf-8").splitlines()]
    assert [(r["created_at"], r["actor"], r["turns"]) for r in rows] == [
        (a["created_at"], a["actor"], a["turns"]) for a in conversation.acts
    ]


def resumed_at_the_probe(
    log: list[Exchange], conversation: Conversation, probed: dict[str, Any] | None
) -> None:
    """The rerun's requests: a probe answering `probed`; then parts that
    repeat exactly the stream up to it, each answered there; then parts
    that carry exactly the rest, the last committing."""
    stream = conversation.stream()
    rerun = restores(log)
    rerun = [e for e in rerun if e.run == 1]
    assert is_probe(rerun[0]) and answered_position(rerun[0]) == probed
    held = 0 if probed is None else 1 + [at for _, at in stream].index(probed)
    sent: list[dict[str, Any]] = []
    for exchange in rerun[1:]:
        within_bounds(exchange)
        before = len(sent)
        sent.extend(items(exchange.body))
        if before < held:
            assert len(sent) <= held, "a part repeats and adds"
            assert answered_position(exchange) == probed
        else:
            assert answered_position(exchange) == stream[len(sent) - 1][1]
    assert sent == [item for item, _ in stream]
    assert rerun[-1].body.get("commit") is True
    assert [e.body.get("commit") for e in rerun[1:-1]] == [None] * (len(rerun) - 2)


@pytest.mark.parametrize("stage", list(STAGES))
def test_an_interrupted_import_resumes_from_the_probed_position(
    enabled: Binary, stage: str
) -> None:
    how, part, last = STAGES[stage]
    conversation = rich()
    assert conversation.digest() == RICH
    archive = archive_of(enabled, conversation)
    restore = restore_id(conversation.id)
    with serve(enabled) as server, forwarding(server.url) as stub:
        lose(stub, how, part)
        message_text = failure(run(enabled, archive, stub.url))
        assert restore in message_text and "run the same import again" in message_text
        applied = content(stub.log, 0)
        assert len(applied) == part
        if how == "cut":
            applied = applied[:-1]
        probed = answered_position(applied[-1]) if applied else None
        if applied:
            assert kind(items(applied[-1].body)[-1]) == last
        state = status(server, conversation.id, restore)
        if stage == "after the commit":
            assert state["state"] == "committed" and state["digest"] == RICH
        else:
            assert state["position"] == probed
            assert state["state"] == ("none" if probed is None else "open")
            assert seen(server, conversation.id) == ABSENT
        stub.next_run()
        assert units(run(enabled, archive, stub.url)) == [conversation.unit()]
        if stage == "after the commit":
            rerun = [e for e in restores(stub.log) if e.run == 1]
            assert len(rerun) == 1 and is_probe(rerun[0])
            assert rerun[0].answer["digest"] == RICH
        else:
            resumed_at_the_probe(stub.log, conversation, probed)
            assert restores(stub.log)[-1].answer["digest"] == RICH
        assert status(server, conversation.id, restore)["digest"] == RICH
        published_once(server, enabled, conversation)


def small(conversation: str = "import-small") -> Conversation:
    """Three turns in two parts: turn 1 fills most of a part."""
    turns = [
        Turn(1, "2026-09-01T11:00:01Z", [message("user", "a" * 60_000)] * 16),
        Turn(3, "2026-09-01T11:00:03Z", [message("assistant", "b" * 60_000)] * 2),
    ]
    entries = [{"turn": 3, "kind": "epoch", "text": "kept", "span": 1}]
    acts = [{"created_at": "2026-09-02T00:00:00Z", "actor": None, "turns": [3]}]
    return Conversation(conversation, turns, entries, acts)


def test_an_archive_changed_after_the_interruption_is_refused_before_the_commit(
    enabled: Binary,
) -> None:
    conversation = small()
    archive = archive_of(enabled, conversation)
    restore = restore_id(conversation.id)
    with serve(enabled) as server, forwarding(server.url) as stub:
        lose(stub, "drop", 1)
        failure(run(enabled, archive, stub.url))
        held = status(server, conversation.id, restore)
        assert held["state"] == "open"
        conversation.turns[0].messages[5] = message("user", "c" * 60_000)
        (archive / "conversations" / conversation.id).rename(archive / "old")
        conversation.write(archive)
        stub.next_run()
        refused = failure(run(enabled, archive, stub.url))
        assert "restore_mismatch" in refused and restore in refused
        assert status(server, conversation.id, restore) == held
        assert seen(server, conversation.id) == ABSENT
        assert all(not e.body.get("commit") for e in restores(stub.log))


def test_a_committed_restore_counts_only_with_the_archive_digest(
    enabled: Binary,
) -> None:
    conversation = small()
    archive = archive_of(enabled, conversation)
    with serve(enabled) as server, forwarding(server.url) as stub:
        assert units(run(enabled, archive, stub.url)) == [conversation.unit()]
        conversation.turns[1].messages[0] = message("assistant", "d" * 60_000)
        (archive / "conversations" / conversation.id).rename(archive / "old")
        conversation.write(archive)
        stub.next_run()
        refused = failure(run(enabled, archive, stub.url))
        assert "already committed" in refused and conversation.digest() in refused
        assert [is_probe(e) for e in restores(stub.log) if e.run == 1] == [True]


def stage(server: Server, conversation: str, body: dict[str, Any]) -> dict[str, Any]:
    """A part sent by hand under the derived restore id."""
    answer: dict[str, Any] = server.post(
        RESTORE, conversation_id=conversation, restore=restore_id(conversation), **body
    )
    assert answer["state"] == "open"
    return answer


LINE = placed(1, record(0, 0, "user", "prompt", "a" * 60_000))
TURN_1 = header(1, "2026-09-01T11:00:01Z", None, 16, 960_000)


@pytest.mark.parametrize(
    "staged",
    [
        # Turn 2 is absent from the archive's sparse numbers 1 and 3.
        {
            "turns": [header(2, "2026-09-01T11:00:02Z", None, 1, 1)],
            "records": [placed(2, record(0, 0, "user", "prompt", "z"))],
        },
        # Turn 1 is complete after one record here, after 16 in the archive.
        {"turns": [{**TURN_1, "records": 1, "bytes": 60_000}], "records": [LINE]},
        # One projection more than the archive holds.
        {
            "projections_from": 1,
            "projections": [{"turn": 1, "kind": "log", "text": "x", "span": 1}],
        },
    ],
    ids=["a sparse turn number", "a turn's record count", "a projection offset"],
)
def test_a_staged_restore_elsewhere_in_the_stream_is_refused_before_writing(
    enabled: Binary, staged: dict[str, Any]
) -> None:
    conversation = small()
    archive = archive_of(enabled, conversation)
    with serve(enabled) as server, forwarding(server.url) as stub:
        if "projections" in staged:
            stream = [item for item, _ in conversation.stream()][:-1]
            parts = [stream[:17], stream[17:]]
            stage(
                server,
                conversation.id,
                {"turns": [parts[0][0]], "records": parts[0][1:]},
            )
            stage(
                server,
                conversation.id,
                {"turns": [parts[1][0]], "records": parts[1][1:3]}
                | {"projections_from": 0, "projections": [parts[1][3]]},
            )
        held = stage(server, conversation.id, staged)
        refused = failure(run(enabled, archive, stub.url))
        assert "not a place in this archive's stream" in refused
        assert [is_probe(e) for e in restores(stub.log)] == [True]
        assert status(server, conversation.id, restore_id(conversation.id)) == {
            **held,
            "stored_bytes": 0,
            "freed_bytes": 0,
        }
        assert seen(server, conversation.id) == ABSENT
