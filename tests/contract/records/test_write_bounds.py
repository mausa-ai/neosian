"""`write_records` at its edges (DESIGN §40.54 items 8.2, 9 and 23): strict
decoding, each field's bounds, the two ceilings, the store's bound and who
may write. A refused request stores nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.contract.records.support import (
    BODY_CEILING,
    BOUND_PREFIX,
    PART_PREFIX,
    ROUTE,
    declared,
    enable,
    nothing,
    probe,
    prompts,
    record,
    refused,
    send,
    write,
)
from tests.contract.support import LIMITED, Binary, Server, serve

SESSION = "s-edge"
ONE = record(0, 0, "user", "prompt", "a")
BASE: dict[str, Any] = {"conversation_id": SESSION, "draft": "d", "records": [ONE]}
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
[[clients.conversations]]
prefix = "g-"
read = true
[[clients]]
actor = "client:writer"
[[clients.conversations]]
prefix = "g-"
append = true
"""


def without(key: str) -> dict[str, Any]:
    return {name: value for name, value in BASE.items() if name != key}


def label(body: dict[str, Any]) -> str:
    """What a body changes in `BASE`, as its case name."""
    changed = {key: value for key, value in body.items() if BASE.get(key) != value}
    missing = [key for key in BASE if key not in body]
    return json.dumps(changed) if changed else "without " + ",".join(missing)


@pytest.mark.parametrize(
    "body",
    [
        {**BASE, "note": 1},
        {**BASE, "turn": 1},
        {**BASE, "conversations": [SESSION]},
        {**BASE, "Draft": "d"},
        without("draft"),
        without("records"),
        {**BASE, "draft": None},
        {**BASE, "draft": 7},
        {**BASE, "records": None},
        {**BASE, "records": {}},
        {**BASE, "records": "[]"},
        {**BASE, "turn_bytes": None},
        {**BASE, "turn_bytes": -1},
        {**BASE, "turn_bytes": "1"},
        {**BASE, "turn_bytes": 1.5},
        {**BASE, "turn_bytes": True},
        {**BASE, "seal": None},
        {**BASE, "seal": 1},
        {**BASE, "seal": "true"},
        {**BASE, "discard": None},
        {**BASE, "discard": 0},
        {**BASE, "actor": 7},
        {**BASE, "actor": ["me"]},
    ],
    ids=label,
)
def test_a_body_is_decoded_strictly(records: Server, body: dict[str, Any]) -> None:
    """Item 8.2: an unknown field, a mistyped value, and `null` where the
    table does not allow it are `400 value_error`."""
    assert refused(records, body)["details"] == {}
    assert probe(records, SESSION, "d") == nothing("d")


def test_a_conversation_id_is_required_and_keeps_its_grammar(records: Server) -> None:
    refused(records, without("conversation_id"))
    refused(records, {**BASE, "conversation_id": None})
    refused(records, {**BASE, "conversation_id": 7})
    for bad in ["", "a/b", "..", "x" * 129]:
        body = {**BASE, "conversation_id": bad}
        refused(records, body, 400, "agent_conversation_id_invalid")
    assert write(records, "x" * 128, "d", [ONE])["state"] == "open"
    assert records.post("store/conversations") == {"conversations": []}


def test_a_draft_id_is_1_to_64_of_its_alphabet(records: Server) -> None:
    for bad in ["", "a" * 65, "Draft", "a.b", "a b", "é", "a/b"]:
        refused(records, {**BASE, "draft": bad})
        refused(records, {**BASE, "draft": bad, "records": []})
    for good in ["a", "a" * 64, "0_-z", "-", "_"]:
        assert write(records, SESSION, good, [ONE])["state"] == "open"
        assert probe(records, SESSION, good)["next_seq"] == 1


@pytest.mark.parametrize(
    "item",
    [
        None,
        "record",
        [],
        {**ONE, "turn": 1},
        {**ONE, "tail": None},
        {**ONE, "truncated": False},
        {**ONE, "note": 1},
        {**ONE, "seq": "0"},
        {**ONE, "seq": -1},
        {**ONE, "seq": None},
        {**ONE, "message": 0.5},
        {**ONE, "chunk": True},
        {**ONE, "size": "1"},
        {**ONE, "text": None},
        {**ONE, "text": 1},
        {**ONE, "role": "User"},
        {**ONE, "role": None},
        {**ONE, "kind": "note"},
        {**ONE, "content": "html"},
        {**ONE, "content": 1},
        {**ONE, "block": "0"},
        {**ONE, "call": -1},
        {**ONE, "call_id": 5},
        {**ONE, "name": 5},
        # Kind against role, and the fields a kind carries.
        {**ONE, "kind": "reply"},
        {**ONE, "kind": "tool_call"},
        {**ONE, "name": "Bash"},
        {**ONE, "kind": "attachment"},
        # Chunk and size consistency.
        {**ONE, "size": 0},
        {**ONE, "text": "", "size": 1},
        {**ONE, "chunk": 1},
        {**ONE, "content": None},
        {**ONE, "message": 1},
        # The bounds of item 3.4.
        {**ONE, "text": "a" * 65_535, "size": 65_535},
        {**ONE, "text": '"' * 32_768, "size": 32_768},
        {**ONE, "call_id": "c" * 1_025},
        {**ONE, "call_id": "é" * 513},
    ],
    ids=lambda item: json.dumps(item)[:60],
)
def test_a_record_is_decoded_and_checked_alone(records: Server, item: Any) -> None:
    assert refused(records, {**BASE, "records": [item]})["details"] == {}
    assert probe(records, SESSION, "d") == nothing("d")


def test_a_record_at_its_bounds_is_stored(records: Server) -> None:
    """Item 3.4: 65,536 bytes of encoded text, quotes included, and 1,024
    bytes of `call_id`; a null key may be omitted and key order is free."""
    full = {**ONE, "text": "a" * 65_534, "size": 65_534}
    assert write(records, SESSION, "full", [full])["next_seq"] == 1
    escaped = {**ONE, "text": '"' * 32_767, "size": 32_767}
    assert write(records, SESSION, "escaped", [escaped])["next_seq"] == 1
    wide = {**ONE, "text": "é" * 32_767, "size": 65_534}
    assert write(records, SESSION, "wide", [wide])["next_seq"] == 1
    named = {**ONE, "role": "tool", "kind": "tool_result", "call_id": "é" * 512}
    assert write(records, SESSION, "named", [named])["next_seq"] == 1
    sparse = {
        "text": "a",
        "size": 1,
        "chunk": 0,
        "content": "text",
        "kind": "prompt",
        "role": "user",
        "message": 0,
        "seq": 0,
    }
    opened = write(records, SESSION, "sparse", [sparse])
    assert write(records, SESSION, "sparse", [ONE]) == {**opened, "stored_bytes": 0}


def test_a_part_holds_at_most_2048_records_with_consecutive_seq(
    records: Server,
) -> None:
    refused(records, {**BASE, "records": prompts(2_049)})
    refused(records, {**BASE, "records": [prompts(3)[0], prompts(3)[2]]})
    refused(records, {**BASE, "records": [prompts(2)[1], prompts(2)[0]]})
    refused(records, {**BASE, "records": [ONE, ONE]})
    assert probe(records, SESSION, "d") == nothing("d")
    assert write(records, SESSION, "d", prompts(2_048))["next_seq"] == 2_048
    refused(records, {**BASE, "records": prompts(2_049, 2_048)})
    more = write(records, SESSION, "d", prompts(2_048, 2_048), seal=True)
    assert (more["next_seq"], more["turn"]["records"]) == (4_096, 4_096)


def test_a_seal_needs_a_record_and_a_complete_last_piece(records: Server) -> None:
    refused(records, {**BASE, "records": [], "seal": True})
    half = record(0, 0, "user", "prompt", "half", size=8)
    refused(records, {**BASE, "records": [half], "seal": True})
    assert probe(records, SESSION, "d") == nothing("d")
    write(records, SESSION, "d", [half])
    refused(records, {**BASE, "records": [], "seal": True})
    rest = record(1, 0, "user", "prompt", "full", content=None, chunk=1, size=8)
    assert write(records, SESSION, "d", [rest], seal=True)["turn"]["bytes"] == 8


def test_a_body_over_4_mib_is_413_before_it_is_parsed(records: Server) -> None:
    """Item 8.2 and the phase's done-when: the ceiling is checked while the
    body is read, inside the bearer gate; the other routes keep 64 MiB."""
    path = "/v1/" + ROUTE
    over = records.request(path, raw=b"x" * (BODY_CEILING + 1))
    assert (over.status, over.body) == (
        413,
        {
            "error": {
                "code": "value_error",
                "message": "request body exceeds 4194304 bytes",
                "details": {},
            }
        },
    )
    at = records.request(path, raw=b"x" * BODY_CEILING)
    assert (at.status, at.body["error"]["code"]) == (400, "value_error")
    assert not at.body["error"]["message"].startswith(PART_PREFIX)
    # The bearer gate answers before any of the body is read.
    unnamed = declared(records, path, BODY_CEILING + 1)
    assert unnamed == ("HTTP/1.1 401 Unauthorized", b"unauthorized")
    # A well-formed request is measured the same way: its bytes, not its records.
    body = json.dumps({**BASE, "records": []}).encode()
    fits = records.request(path, raw=body + b" " * (BODY_CEILING - len(body)))
    assert (fits.status, fits.body) == (200, nothing("d"))
    long = records.request(path, raw=body + b" " * (BODY_CEILING + 1 - len(body)))
    assert (long.status, long.body["error"]["message"]) == (
        413,
        "request body exceeds 4194304 bytes",
    )
    legacy = records.request(
        "/v1/conversation/append_turn", raw=b"x" * (BODY_CEILING + 1)
    )
    assert (legacy.status, legacy.body["error"]["code"]) == (400, "value_error")


def test_a_turn_past_the_store_bound_is_413_naming_the_setting(
    enabled: Binary,
) -> None:
    """Items 8.2, 9 and 23: at the opening by the stated total, while
    appending, and at a seal under a bound lowered since."""
    body = {"conversation_id": SESSION, "draft": "d"}
    first = record(0, 0, "user", "prompt", "a" * 40_000)
    second = record(1, 1, "assistant", "reply", "b" * 30_000)

    def kept(server: Server, request: dict[str, Any], held: int) -> None:
        error = refused(server, request, 413)
        assert error["message"] == (
            "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded:"
            f" {held} > 65536; this store does not keep a turn this large"
        )
        assert error["details"] == {}
        assert error["message"].startswith(BOUND_PREFIX)
        assert not error["message"].startswith(PART_PREFIX)

    low = {"NEOSIAN_MAX_RECORD_TURN_BYTES": "65536"}
    with serve(enabled, extra_env=low) as server:
        kept(server, {**body, "records": [first], "turn_bytes": 65_537}, 65_537)
        assert probe(server, SESSION, "d") == nothing("d")
        stated = write(server, SESSION, "stated", prompts(1), turn_bytes=65_536)
        assert stated["state"] == "open"
        assert write(server, SESSION, "d", [first])["next_seq"] == 1
        kept(server, {**body, "records": [second]}, 70_000)
        kept(server, {**body, "records": [second], "seal": True}, 70_000)
        assert probe(server, SESSION, "d")["next_seq"] == 1
        write(server, SESSION, "d", [], discard=True)
    with serve(enabled) as server:
        assert write(server, SESSION, "d", [first, second])["next_seq"] == 2
    with serve(enabled, extra_env=low) as server:
        kept(server, {**body, "records": [], "seal": True}, 70_000)
        assert probe(server, SESSION, "d")["state"] == "open"
    with serve(enabled) as server:
        sealed = write(server, SESSION, "d", [], seal=True)
        assert sealed["turn"]["bytes"] == 70_000


def test_a_constrained_token_writes_only_inside_its_allowance(
    records: Server,
) -> None:
    inside = send(records, {**BASE, "conversation_id": "allowed-1"}, token=LIMITED)
    assert (inside.status, inside.body["state"]) == (200, "open")
    for body in [
        BASE,
        without("conversation_id"),
        {**BASE, "conversation_id": 7},
        {**BASE, "conversation_id": "other", "conversations": ["allowed-1"]},
    ]:
        outside = send(records, body, token=LIMITED)
        assert (outside.status, outside.body["error"]["code"]) == (403, "forbidden")
    assert probe(records, SESSION, "d") == nothing("d")
    turns = records.post("conversation/read_turns", conversation_id="allowed-1")
    assert turns["turns"] == []


def test_a_grant_must_allow_append(enabled: Binary, tmp_path: Path) -> None:
    """Item 8.2: `write_records` needs append; a read grant is not enough."""
    grants = tmp_path / "grants.toml"
    grants.write_text(GRANTS, encoding="utf-8")
    env = {
        "NEOSIAN_SERVE_GRANTS": str(grants),
        "NEOSIAN_SERVE_TOKEN": "client:contract=reader,client:writer=writer",
    }
    body = {**BASE, "conversation_id": "g-1"}
    with serve(enabled, extra_env=env) as server:
        reader = send(server, body, token="reader")
        assert (reader.status, reader.body["error"]["code"]) == (403, "forbidden")
        nobody = send(server, {**body, "records": []}, token="reader")
        assert nobody.status == 403
        writer = send(server, body, token="writer")
        assert (writer.status, writer.body["state"]) == (200, "open")
        beyond = send(server, BASE, token="writer")
        assert (beyond.status, beyond.body["error"]["code"]) == (403, "forbidden")
        sealed = send(server, {**body, "records": [], "seal": True}, token="writer")
        assert sealed.body["turn"]["actor"] == "client:writer"
        read = server.request(
            "/v1/conversation/read_turns", {"conversation_id": "g-1"}, token="reader"
        )
        assert [turn["turn"] for turn in read.body["turns"]] == [1]


def test_a_store_without_records_answers_its_own_refusal(binary: Binary) -> None:
    """Rulings D6 and D4: `agent_configuration_error` to every JSON object
    until the marker exists; a running server needs no restart. The
    handshake does not name the capability before the slice that adds it."""
    absent = {
        "code": "agent_configuration_error",
        "message": "records are not enabled on this store;"
        " run `neosian records enable`",
        "details": {},
    }
    with serve(binary) as server:
        for body in [BASE, {}, {**BASE, "note": 1}, {**BASE, "draft": "D"}]:
            assert refused(server, body, 400, "agent_configuration_error") == absent
        limited = send(server, BASE, token=LIMITED)
        assert limited.status == 403
        for malformed in [b"x", b"[]", b"null"]:
            reply = server.request("/v1/" + ROUTE, raw=malformed)
            assert (reply.status, reply.body["error"]["code"]) == (400, "value_error")
        assert not (binary.home / "conversations").exists()
        enable(binary)
        assert write(server, SESSION, "d", [ONE], seal=True)["turn"]["turn"] == 1
        capabilities = server.request("/v1/capabilities", method="GET")
        assert "turn_records" not in capabilities.body
