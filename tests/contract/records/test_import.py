"""`neosian import DIR --url` restores in parts where the server keeps
records (the T1e2e slice of the T1e2 plan, DESIGN §40.54 items 12 and 22).

Against a server that declares `turn_records` version 1, each conversation
holding a turn goes as one restore under the id item 22 derives: a probe,
then parts of at most 983,040 bytes and 2,048 items in stream order, the
last committing, and the commit's unit digest is the one computed here
from the archive's rows. More than 500 erasure acts arrive in stored
order. A refusal stops the import with its status, code and
`Retry-After`, the staged restore kept for the rerun. A server without
the capability gets one `restore_conversation` per conversation with the
bytes the released client sends, and a conversation without turns goes
whole on a server with it. A mixed archive cut inside its conversation is
finished by a rerun that passes over the scope it already restored, after
reading it back equal, and refuses a scope that differs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.contract.landing.stub import Rule, forwarding
from tests.contract.records.importing import (
    PART_ITEMS,
    answered_position,
    failure,
    is_probe,
    items,
    restore_id,
    restores,
    rich,
    run,
    units,
    within_bounds,
)
from tests.contract.records.restoring import RESTORE, status
from tests.contract.records.test_import_resume import (
    RICH,
    archive_of,
    published_once,
    small,
)
from tests.contract.support import Binary, serve

SCOPE = "user:mixed"


def test_an_import_restores_each_conversation_in_bounded_parts(
    enabled: Binary,
) -> None:
    conversation = rich()
    assert conversation.digest() == RICH
    archive = archive_of(enabled, conversation)
    with serve(enabled) as server, forwarding(server.url) as stub:
        assert units(run(enabled, archive, stub.url)) == [conversation.unit()]
        sent = restores(stub.log)
        assert all(e.body["restore"] == restore_id(conversation.id) for e in sent)
        assert is_probe(sent[0]) and sent[0].answer["state"] == "none"
        stream = conversation.stream()
        carried: list[dict[str, Any]] = []
        for exchange in sent[1:]:
            within_bounds(exchange)
            carried.extend(items(exchange.body))
            assert answered_position(exchange) == stream[len(carried) - 1][1]
        assert carried == [item for item, _ in stream]
        assert [len(items(e.body)) for e in sent[1:]][-2] == PART_ITEMS
        assert [e.body.get("commit") for e in sent[1:]] == [None] * 5 + [True]
        assert sent[-1].answer["state"] == "committed"
        assert sent[-1].answer["digest"] == RICH
        assert not [e for e in stub.log if e.route == "store/restore_conversation"]
        published_once(server, enabled, conversation)


def test_a_refusal_stops_the_import_with_its_status_and_retry_after(
    enabled: Binary,
) -> None:
    conversation = small()
    archive = archive_of(enabled, conversation)
    restore = restore_id(conversation.id)
    with serve(enabled) as server, forwarding(server.url) as stub:
        stub.add(
            Rule(
                when=lambda e: e.route == RESTORE and e.nth == 3,
                kind="answer",
                status=429,
                body={"error": {"code": "rate_limited", "message": "slow down"}},
                headers={"Retry-After": "7"},
            )
        )
        refused = failure(run(enabled, archive, stub.url))
        assert "429 (rate_limited): slow down, retry after 7 seconds" in refused
        assert restore in refused and "run the same import again" in refused
        assert status(server, conversation.id, restore)["position"] == (
            answered_position(restores(stub.log)[1])
        )
        stub.next_run()
        assert units(run(enabled, archive, stub.url)) == [conversation.unit()]
        assert status(server, conversation.id, restore)["digest"] == (
            conversation.digest()
        )


def test_a_server_without_turn_records_gets_the_whole_restore(binary: Binary) -> None:
    conversation = small()
    archive = archive_of(binary, conversation)
    with serve(binary) as server, forwarding(server.url) as stub:
        assert units(run(binary, archive, stub.url)) == [conversation.unit()]
        assert not restores(stub.log)
        whole = [e for e in stub.log if e.route == "store/restore_conversation"]
        assert len(whole) == 1
        # The released client sends its legacy bodies indented by two.
        expected = json.dumps(
            {
                "conversation_id": conversation.id,
                "turns": [turn.read(conversation.id) for turn in conversation.turns],
                "projections": conversation.ordered_projections(),
                "redactions": [
                    {"conversation_id": conversation.id, "turns": a["turns"]}
                    | {"actor": a["actor"], "created_at": a["created_at"]}
                    for a in conversation.acts
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        assert whole[0].raw == expected.encode()


def test_a_conversation_without_turns_goes_whole_and_a_rerun_reads_it_back(
    enabled: Binary,
) -> None:
    conversation = small("import-entries")
    conversation.turns = []
    archive = archive_of(enabled, conversation)
    with serve(enabled) as server, forwarding(server.url) as stub:
        named = ("--conversation", conversation.id)
        assert units(run(enabled, archive, stub.url, *named)) == [conversation.unit()]
        assert not restores(stub.log)
        assert (
            len([e for e in stub.log if e.route == "store/restore_conversation"]) == 1
        )
        stub.next_run()
        assert units(run(enabled, archive, stub.url, *named)) == [conversation.unit()]
        rerun = [e for e in stub.log if e.run == 1]
        assert not [e for e in rerun if e.route.startswith("store/restore")]
        server.post(
            "conversation/append_projections",
            conversation_id=conversation.id,
            entries=[{"turn": 3, "kind": "log", "text": "added"}],
        )
        refused = failure(run(enabled, archive, stub.url, *named))
        assert "target_occupied" in refused


def scoped(binary: Binary, archive: Path) -> dict[str, Any]:
    """Two documents of one scope in the archive, written by the CLI."""
    for path, text in [("one", "first note"), ("two", "second note")]:
        made = binary.run(
            "memory", "create", f"/memories/{path}", "--root", str(archive),
            "--scope", SCOPE, "--content", "-", stdin=text,
        )  # fmt: skip
        assert made.returncode == 0, made.stderr
    return {
        "kind": "scope",
        "name": SCOPE,
        "documents": 2,
        "versions": 2,
        "redactions": 0,
        "turns": 0,
        "projections": 0,
    }


def test_a_mixed_archive_cut_inside_its_conversation_is_finished_by_a_rerun(
    enabled: Binary,
) -> None:
    conversation = small()
    archive = archive_of(enabled, conversation)
    scope = scoped(enabled, archive)
    restore = restore_id(conversation.id)
    with serve(enabled) as server, forwarding(server.url) as stub:
        stub.add(Rule(when=lambda e: e.route == RESTORE and e.nth == 2, kind="drop"))
        failure(run(enabled, archive, stub.url))
        # The probe is the preflight's; the scope goes first after it.
        assert [e.route for e in stub.log if e.route.startswith("store/restore_")] == [
            RESTORE,
            "store/restore_scope",
            RESTORE,
        ]
        held = status(server, conversation.id, restore)["position"]
        stub.next_run()
        assert units(run(enabled, archive, stub.url)) == [scope, conversation.unit()]
        rerun = [e for e in stub.log if e.run == 1 and e.route.startswith("store/")]
        assert "store/restore_scope" not in [e.route for e in rerun]
        assert answered_position(restores(rerun)[0]) == held
        assert status(server, conversation.id, restore)["digest"] == (
            conversation.digest()
        )
        # A scope that holds more than the archive's is not passed over.
        server.post("memory/write", scope=SCOPE, path="three", content="drift")
        stub.next_run()
        refused = failure(run(enabled, archive, stub.url))
        assert "target_occupied" in refused and SCOPE in refused
        assert not [e for e in restores(stub.log) if e.run == 2 and not is_probe(e)]
