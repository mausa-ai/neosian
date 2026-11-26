"""The limits of `store/restore_records` (DESIGN §40.54 items 8.2, 12 and
23, rulings D6 and R2): the 4 MiB ceiling of a part, the store's bound on
one turn, a store without records, and the route as the operator's alone.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from tests.contract.records.restoring import (
    FIRST,
    HEADERS,
    ID,
    ONE,
    RESTORE,
    answer,
    header,
    part,
    placed,
    position,
    refused,
    send,
    status,
)
from tests.contract.records.searching import (
    ABSENT,
    UNGRANTED,
    WHOLE_STORE,
    forbidden,
    refusal,
    store_lock,
)
from tests.contract.records.support import (
    BODY_CEILING,
    BOUND_PREFIX,
    PART_PREFIX,
    compact,
    declared,
    record,
)
from tests.contract.support import ADMIN, LIMITED, Binary, Server, serve

SESSION = "s-edge"
BASE: dict[str, Any] = {"conversation_id": SESSION, "restore": "r"}
HEAD = header(1, "2026-10-02T09:15:00Z", "a", 1, 1)
ITEM = placed(1, record(0, 0, "user", "prompt", "a"))
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
operator = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
prefix = "s-"
read = true
append = true
"""


def test_a_body_over_4_mib_is_413_before_it_is_parsed(records: Server) -> None:
    """Item 8.2: a part is at most 4,194,304 bytes, checked while the body
    is read and inside the bearer gate; ruling R2: a declared length names
    this route's ceiling, also when it is past 64 MiB."""
    path = "/v1/" + RESTORE
    limit = {
        "error": {
            "code": "value_error",
            "message": "request body exceeds 4194304 bytes",
            "details": {},
        }
    }
    over = records.request(path, raw=b"x" * (BODY_CEILING + 1))
    assert (over.status, over.body) == (413, limit)
    at = records.request(path, raw=b"x" * BODY_CEILING)
    assert (at.status, at.body["error"]["message"]) == (
        400,
        "request body must be JSON",
    )
    # A well-formed request is measured the same way: its bytes.
    body = json.dumps(BASE).encode()
    fits = records.request(path, raw=body + b" " * (BODY_CEILING - len(body)))
    assert (fits.status, fits.body) == (200, answer("r", "none", None))
    long = records.request(path, raw=body + b" " * (BODY_CEILING + 1 - len(body)))
    assert (long.status, long.body) == (413, limit)
    # With a token the declared length alone is refused, naming this
    # route's ceiling; without one the bearer gate answers first.
    large = "HTTP/1.1 413 Payload Too Large"
    for length in [67_108_865, 1 << 40]:
        assert declared(records, path, length, token=ADMIN) == (large, compact(limit))
        unnamed = declared(records, path, length)
        assert unnamed == ("HTTP/1.1 401 Unauthorized", b"unauthorized")
    # Between the two ceilings the body is what is measured: without a
    # bearer the gate answers first, and a legacy route reads the same
    # bytes and refuses them as what they are.
    unnamed = declared(records, path, BODY_CEILING + 1)
    assert unnamed == ("HTTP/1.1 401 Unauthorized", b"unauthorized")
    read = records.request(
        "/v1/conversation/append_turn", raw=b"x" * (BODY_CEILING + 1)
    )
    assert (read.status, read.body["error"]["message"]) == (
        400,
        "request body must be JSON",
    )
    # A constrained token's allowance is read from the body, so its
    # declared length answers first; with a body it is the 403.
    assert declared(records, path, 1 << 40, token=LIMITED) == (large, compact(limit))
    small = records.request(path, BASE, token=LIMITED)
    assert (small.status, small.body["error"]["code"]) == (403, "forbidden")
    assert status(records, SESSION, "r") == answer("r", "none", None)


def test_a_header_past_the_store_bound_is_413_when_it_arrives(
    enabled: Binary,
) -> None:
    """Items 8.2, 12 and 23: a header whose `bytes` exceed
    `NEOSIAN_MAX_RECORD_TURN_BYTES` is refused with the store's own 413
    before any of its records, and a part holding it stages nothing."""
    low = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    big = header(2, "2026-10-02T09:20:00Z", "a", 2, 65_537)
    at_bound = header(2, "2026-10-02T09:20:00Z", "a", 2, 65_536)
    with serve(enabled, extra_env=low) as server:
        parts: list[dict[str, Any]] = [
            {"turns": [big]},
            {"turns": [HEAD, big], "records": [ITEM]},
            {"turns": [big], "commit": True},
        ]
        for more in parts:
            error = refused(server, {**BASE, **more}, 413)
            assert error["message"] == (
                "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded:"
                " 65537 > 65536; this store does not keep a turn this large"
            )
            assert error["details"] == {}
            assert error["message"].startswith(BOUND_PREFIX)
            assert not error["message"].startswith(PART_PREFIX)
        assert status(server, SESSION, "r") == answer("r", "none", None)
        opened = part(server, SESSION, "r", turns=[HEAD, at_bound], records=[ITEM])
        assert opened["position"] == position(2, 0, False)
    with serve(enabled) as server:
        assert part(server, "s-other", "r", turns=[big])["state"] == "open"


def test_a_store_without_records_answers_its_own_refusal(server: Server) -> None:
    """Ruling D6: `400 agent_configuration_error` whatever the body holds."""
    for body in [{}, BASE, {**BASE, **FIRST, "commit": True}, {"nonsense": 1}]:
        assert refusal(server, RESTORE, body, 400, "agent_configuration_error") == (
            ABSENT
        )
    assert forbidden(server, RESTORE, BASE, LIMITED) == WHOLE_STORE
    assert server.post("store/conversations") == {"conversations": []}


def test_a_restore_in_parts_is_the_operators_alone(
    enabled: Binary, tmp_path: Path
) -> None:
    """Item 8.2: a whole-store route. A constrained token is refused
    whatever its body names, the conversation inside its allowance
    included, and under grants only an operator restores. A refusal
    answers while the store's lock is held, so nothing was read for it."""
    home = enabled.home
    inside = {"conversation_id": "allowed-1", "restore": ID}
    bodies: list[dict[str, Any]] = [
        inside,
        {**inside, "turns": HEADERS[:1], "records": ONE},
        {**inside, "commit": True},
        {**inside, "discard": True},
        {**inside, "conversations": ["allowed-1"], "scope": "user:allowed"},
        {**inside, "nonsense": 1},
        {},
    ]
    with serve(enabled) as server:
        part(server, "allowed-1", ID, turns=HEADERS[:1], records=ONE)
        at = answer(ID, "open", position(1, 3, True))
        for body in bodies:
            assert forbidden(server, RESTORE, body, LIMITED) == WHOLE_STORE, body
        with store_lock(home):
            for body in bodies:
                assert forbidden(server, RESTORE, body, LIMITED) == WHOLE_STORE
            answers: list[Any] = []
            waiting = threading.Thread(
                target=lambda: answers.append(send(server, "allowed-1", ID))
            )
            waiting.start()
            waiting.join(0.5)
            assert waiting.is_alive() and answers == []
        waiting.join(10)
        assert (answers[0].status, answers[0].body) == (200, at)
    grants = tmp_path / "grants.toml"
    grants.write_text(
        GRANTS + '[[clients]]\nactor = "client:fenced"\noperator = true\n',
        encoding="utf-8",
    )
    env = {
        "NEOSIAN_SERVE_GRANTS": str(grants),
        "NEOSIAN_SERVE_TOKEN": (
            f"client:contract={ADMIN},client:reader=reader,client:stranger=stranger,"
            "client:fenced+allowed-=fenced"
        ),
    }
    with serve(enabled, extra_env=env) as server:
        named = {"conversation_id": "s-1", "restore": ID}
        with store_lock(home):
            for token in ["reader", "stranger"]:
                for body in [*bodies, named, {**named, "discard": True}]:
                    assert forbidden(server, RESTORE, body, token) == UNGRANTED
            # An operator's grant does not widen a constrained token.
            for body in bodies:
                assert forbidden(server, RESTORE, body, "fenced") == WHOLE_STORE
        # Nothing a refused client sent was discarded, staged or committed.
        assert status(server, "allowed-1", ID) == at
        assert status(server, "s-1", ID) == answer(ID, "none", None)
        assert part(server, "allowed-1", ID, discard=True)["state"] == "discarded"
