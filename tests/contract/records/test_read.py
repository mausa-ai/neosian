"""`conversation/read_records`, its request (DESIGN §40.54 items 8.2 and 10):
every field of the table with its default, its bounds and its refusal, the
order of the checks before the store is read, and what a token that may
not read a conversation learns about it, which is nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.contract.records.reading import (
    READ,
    all_answered,
    header,
    message,
    post,
    read,
    refusal,
)
from tests.contract.records.support import compact, declared, enable, record, write
from tests.contract.support import ADMIN, LIMITED, Binary, Server, serve

SESSION = "s-read"
BASE: dict[str, Any] = {"conversation_id": SESSION}
ONE = [
    record(0, 0, "user", "prompt", "hello"),
    record(1, 1, "assistant", "reply", "hi"),
]
GRANTS = """grant_format = 1
[[clients]]
actor = "client:contract"
[[clients.conversations]]
prefix = "g-"
read = true
append = true
[[clients]]
actor = "client:reader"
[[clients.conversations]]
ids = ["g-1"]
read = true
[[clients]]
actor = "client:writer"
[[clients.conversations]]
prefix = "g-"
append = true
"""
EMPTY: dict[str, Any] = {"turns": [], "records": [], "continuation": None}
INTEGERS = ["turn", "after", "through", "seq", "page_bytes"]


def two_turns(server: Server, conversation: str = SESSION) -> list[dict[str, Any]]:
    """Two sealed turns of `ONE`; their headers as `read_turns` dates them."""
    for draft in ["a", "b"]:
        write(server, conversation, draft, ONE, seal=True)
    turns = server.post("conversation/read_turns", conversation_id=conversation)
    return [header(turn, 2, 7) for turn in turns["turns"]]


def test_an_absent_field_takes_its_default(records: Server) -> None:
    """`after` 0, no `through`, every kind, no heads, whole records, a page
    of 983,040 bytes: the whole conversation in one answer."""
    headers = two_turns(records)
    whole = {
        "turns": headers,
        "records": all_answered(1, ONE) + all_answered(2, ONE),
        "continuation": None,
    }
    assert read(records, SESSION) == whole
    stated = read(records, SESSION, after=0, page_bytes=983_040)
    assert stated == whole
    unpieced = read(records, SESSION, turn=1, seq=0, piece=False)
    assert unpieced == read(records, SESSION, turn=1)
    assert read(records, SESSION, heads={}) == whole
    assert read(records, "s-none") == EMPTY


def test_turn_after_through_and_seq_select_turns_and_a_start(records: Server) -> None:
    headers = two_turns(records)
    first, second = all_answered(1, ONE), all_answered(2, ONE)

    def answer(turns: list[Any], items: list[Any]) -> dict[str, Any]:
        return {"turns": turns, "records": items, "continuation": None}

    assert read(records, SESSION, turn=1) == answer(headers[:1], first)
    assert read(records, SESSION, turn=2) == answer(headers[1:], second)
    assert read(records, SESSION, turn=3) == EMPTY
    assert read(records, SESSION, after=1) == answer(headers[1:], second)
    assert read(records, SESSION, after=2) == EMPTY
    assert read(records, SESSION, through=1) == answer(headers[:1], first)
    assert read(records, SESSION, after=1, through=1) == EMPTY
    assert read(records, SESSION, after=1, through=2) == answer(headers[1:], second)
    assert read(records, SESSION, through=9) == answer(headers, first + second)
    assert read(records, SESSION, turn=1, seq=0) == answer(headers[:1], first)
    assert read(records, SESSION, turn=2, seq=1) == answer(headers[1:], second[1:])
    # Past the turn's last record its header is still a turn the page reaches.
    assert read(records, SESSION, turn=2, seq=2) == answer(headers[1:], [])
    large = 9_223_372_036_854_775_807
    assert read(records, SESSION, after=large) == EMPTY
    assert read(records, SESSION, turn=large, seq=large) == EMPTY
    assert read(records, SESSION, through=large) == answer(headers, first + second)


@pytest.mark.parametrize(
    ("more", "sentence"),
    [
        ({"turn": 1, "after": 1}, "turn excludes after and through"),
        ({"turn": 1, "after": 0}, "turn excludes after and through"),
        ({"turn": 1, "through": 1}, "turn excludes after and through"),
        ({"turn": 1, "after": 0, "through": 2}, "turn excludes after and through"),
        ({"turn": 0}, "turn and through are at least 1"),
        ({"through": 0}, "turn and through are at least 1"),
        ({"seq": 0}, "seq requires turn"),
        ({"after": 1, "seq": 1}, "seq requires turn"),
        ({"piece": True}, "piece requires turn and seq"),
        ({"turn": 1, "piece": True}, "piece requires turn and seq"),
        ({"piece": False}, "piece requires turn and seq"),
        ({"turn": 1, "piece": False}, "piece requires turn and seq"),
        ({"after": 1, "piece": False}, "piece requires turn and seq"),
        ({"seq": 1, "piece": False}, "seq requires turn"),
        ({"seq": 1, "piece": True}, "seq requires turn"),
        ({"page_bytes": 0}, "page_bytes must be >= 1"),
        ({"after": 18_446_744_073_709_551_615}, "after must be below 2^64 - 1"),
        ({"kinds": ["prompts"]}, 'unknown record kind "prompts"'),
        ({"kinds": ["prompt", "Reply"]}, 'unknown record kind "Reply"'),
        ({"kinds": ["prompt", 7]}, "parameter 'kinds' must be a list of kinds"),
        ({"kinds": [None]}, "parameter 'kinds' must be a list of kinds"),
        ({"heads": {"answer": 5}}, 'unknown record kind "answer"'),
        ({"heads": {"tool_result": 0}}, 'heads "tool_result" must be 1 to 32768'),
        ({"heads": {"reply": 32_769}}, 'heads "reply" must be 1 to 32768'),
        ({"heads": {"reply": "9"}}, 'heads "reply" must be 1 to 32768'),
        ({"heads": {"reply": None}}, 'heads "reply" must be 1 to 32768'),
        ({"heads": {"reply": 2.5}}, 'heads "reply" must be 1 to 32768'),
        ({"heads": {"reply": -1}}, 'heads "reply" must be 1 to 32768'),
        ({"heads": {"reply": True}}, 'heads "reply" must be 1 to 32768'),
        ({"continuation": ""}, "continuation must contain 1..4096 bytes"),
        ({"continuation": "a" * 4_097}, "continuation must contain 1..4096 bytes"),
        ({"continuation": "é" * 2_049}, "continuation must contain 1..4096 bytes"),
    ],
    ids=lambda value: compact(value).decode()[:60] if isinstance(value, dict) else "",
)
def test_each_rule_between_the_fields_refuses_in_its_own_words(
    records: Server, more: dict[str, Any], sentence: str
) -> None:
    two_turns(records)
    expected = sentence.replace("2^64 - 1", str(2**64 - 1))
    assert message(records, {**BASE, **more}) == expected


def test_a_body_is_decoded_strictly(records: Server) -> None:
    """Item 8.2: an unknown field and a mistyped value are `value_error`,
    and `null` is accepted nowhere: item 10's table allows it on no field.
    Ruling R11: a named field counts even at its default, so the rules
    between fields (above) refuse `after: 0` and `piece: false` alike."""
    two_turns(records)
    for name in ["limit", "conversations", "Turn", "page", "draft", "scope"]:
        unknown = message(records, {**BASE, name: 1})
        assert unknown == f"unknown parameter: '{name}'"
    assert message(records, {}) == "missing parameter: 'conversation_id'"
    wrong: list[Any] = [None, 7, ["s"], {"id": "s"}, True]
    for value in wrong:
        mistyped = message(records, {"conversation_id": value})
        assert mistyped == "parameter 'conversation_id' must be a string"
    for name in INTEGERS:
        wrong = [None, -1, 1.5, "1", True, [1], 1e30, 2**64]
        for value in wrong:
            mistyped = message(records, {**BASE, name: value})
            assert mistyped == f"parameter '{name}' must be a non-negative integer"
    wrong = [None, 0, 1, "true", []]
    for value in wrong:
        mistyped = message(records, {**BASE, "turn": 1, "seq": 0, "piece": value})
        assert mistyped == "parameter 'piece' must be a boolean"
    wrong = [None, "prompt", {"prompt": True}, 1]
    for value in wrong:
        mistyped = message(records, {**BASE, "kinds": value})
        assert mistyped == "parameter 'kinds' must be a list"
    wrong = [None, ["tool_result"], "tool_result", 4]
    for value in wrong:
        mistyped = message(records, {**BASE, "heads": value})
        assert mistyped == "parameter 'heads' must be an object"
    wrong = [None, 7, ["a"], {}]
    for value in wrong:
        mistyped = message(records, {**BASE, "continuation": value})
        assert mistyped == "parameter 'continuation' must be a string"
    # The first unknown key in the body's own order is the one named.
    both = message(records, {"zeta": 1, **BASE, "alpha": 2})
    assert both == "unknown parameter: 'zeta'"
    # The id grammar is the stores' own refusal, after the body's rules.
    reason = "must match [A-Za-z0-9_.-]{1,128} — one flat segment"
    for name in ["no/slash", "a b"]:
        grammar = refusal(
            records, {"conversation_id": name}, 400, "agent_conversation_id_invalid"
        )
        assert grammar == {
            "code": "agent_conversation_id_invalid",
            "message": f"Invalid conversation id '{name}': {reason}",
            "details": {"conversation_id": name, "reason": reason},
        }
    late = message(records, {"conversation_id": "no/slash", "turn": 0})
    assert late == "turn and through are at least 1"


def error(code: str, sentence: str) -> bytes:
    return compact({"error": {"code": code, "message": sentence, "details": {}}})


def test_the_checks_come_in_order_before_the_store_is_read(binary: Binary) -> None:
    """Bearer, body ceiling, JSON, object, allowance, records enabled,
    strict decoding, store. Shown on a store that keeps no records, so each
    later check would have answered otherwise."""
    absent = error(
        "agent_configuration_error",
        "records are not enabled on this store; run `neosian records enable`",
    )
    whole = error("value_error", "request body exceeds 67108864 bytes")
    path, large = "/v1/" + READ, "HTTP/1.1 413 Payload Too Large"
    with serve(binary) as server:
        # No bearer: nothing else is looked at, whatever the body declares.
        unnamed = declared(server, path, 67_108_865)
        assert unnamed == ("HTTP/1.1 401 Unauthorized", b"unauthorized")
        nobody = server.request(path, raw=b"x", authenticated=False)
        assert (nobody.status, nobody.body) == (401, "unauthorized")
        wrong = server.request(path, BASE, token="not-a-token")
        assert (wrong.status, wrong.body) == (401, "unauthorized")
        # The ceiling is the general 64 MiB, not a part's 4 MiB.
        assert declared(server, path, 67_108_865, token=ADMIN) == (large, whole)
        assert declared(server, path, 1 << 40, token=ADMIN) == (large, whole)
        padded = b'{"conversation_id":"s-read"' + b" " * 4_194_304 + b"}"
        over_a_part = server.request(path, raw=padded)
        assert (over_a_part.status, compact(over_a_part.body)) == (400, absent)
        # JSON, then an object, before the store is asked anything.
        for body, sentence in [
            (b"x", "request body must be JSON"),
            (b"", "request body must be JSON"),
            (b'{"conversation_id":', "request body must be JSON"),
            (b"[]", "request body must be an object"),
            (b"null", "request body must be an object"),
            (b'"s-read"', "request body must be an object"),
            (b"7", "request body must be an object"),
        ]:
            malformed = server.request(path, raw=body)
            assert malformed.status == 400, body
            assert compact(malformed.body) == error("value_error", sentence), body
        # The allowance, before the store says whether it keeps records and
        # before the body is decoded: an unknown field changes nothing.
        outside = error(
            "forbidden", "this token's allowance does not reach conversation 's-read'"
        )
        for asked in [BASE, {**BASE, "limit": 1}, {**BASE, "turn": 0}]:
            limited = post(server, asked, token=LIMITED)
            assert (limited.status, compact(limited.body)) == (403, outside)
        # Records enabled, before the body is decoded.
        for asked in [BASE, {}, {**BASE, "limit": 1}, {"conversation_id": 7}]:
            reply = post(server, asked)
            assert (reply.status, compact(reply.body)) == (400, absent), asked
        assert not (binary.home / "conversations").exists()
        # Ruling D4: the marker is read per request, so no restart is needed.
        enable(binary)
        assert read(server, SESSION) == EMPTY
        assert message(server, {**BASE, "limit": 1}) == "unknown parameter: 'limit'"
        capabilities = server.request("/v1/capabilities", method="GET")
        assert "turn_records" not in capabilities.body


def test_a_constrained_token_reads_only_inside_its_allowance(records: Server) -> None:
    """Item 8.2: read permission on the conversation named. The refusal is
    the same sentence whether the conversation exists or not."""
    headers = two_turns(records, "allowed-1")
    two_turns(records, "other")
    inside = post(records, {"conversation_id": "allowed-1"}, token=LIMITED)
    assert (inside.status, inside.body["turns"]) == (200, headers)
    whole = b'{"error":{"code":"forbidden","message":"this token\'s allowance'
    whole += b' does not reach the whole store","details":{}}}'

    def outside(name: str) -> bytes:
        sentence = f"this token's allowance does not reach conversation '{name}'"
        return error("forbidden", sentence)

    for body, expected in [
        ({"conversation_id": "other"}, outside("other")),
        ({"conversation_id": "ghost"}, outside("ghost")),
        ({"conversation_id": "other", "turn": 0}, outside("other")),
        ({"conversation_id": "other", "limit": 1}, outside("other")),
        (
            {"conversation_id": "other", "conversations": ["allowed-1"]},
            outside("other"),
        ),
        ({"conversation_id": "other", "scope": "user:allowed"}, outside("other")),
        ({}, whole),
        ({"conversation_id": 7}, whole),
        ({"conversation_id": None}, whole),
        ({"conversations": ["allowed-1"]}, None),
    ]:
        reply = post(records, body, token=LIMITED)
        if expected is None:
            # Naming an allowed list is no way around the strict body.
            assert (reply.status, reply.body["error"]["code"]) == (400, "value_error")
            continue
        assert (reply.status, compact(reply.body)) == (403, expected), body


def test_a_grant_must_allow_read_on_the_conversation(
    enabled: Binary, tmp_path: Path
) -> None:
    """Item 8.2 under grants: `read` on the conversation named. A client
    without it is answered the same bytes for a conversation that exists,
    one that does not, a malformed request and a store read that would have
    failed, so it learns nothing about any of them."""
    grants = tmp_path / "grants.toml"
    grants.write_text(GRANTS, encoding="utf-8")
    env = {
        "NEOSIAN_SERVE_GRANTS": str(grants),
        "NEOSIAN_SERVE_TOKEN": (
            f"client:contract={ADMIN},client:reader=reader,client:writer=writer,"
            "client:stranger=stranger"
        ),
    }
    denied = error(
        "forbidden",
        "this token's allowance does not reach"
        " the requested operation under this client's grants",
    )
    with serve(enabled, extra_env=env) as server:
        headers = two_turns(server, "g-1")
        two_turns(server, "g-2")
        reader = post(server, {"conversation_id": "g-1"}, token="reader")
        assert (reader.status, reader.body["turns"]) == (200, headers)
        assert reader.body["records"] == all_answered(1, ONE) + all_answered(2, ONE)
        for token, body in [
            ("reader", {"conversation_id": "g-2"}),
            ("reader", {"conversation_id": "g-9"}),
            ("reader", {"conversation_id": "g-2", "turn": 0}),
            ("reader", {"conversation_id": "g-2", "limit": 1}),
            ("reader", {"conversation_id": "g-2", "continuation": "x"}),
            ("reader", {"conversation_id": "g-2", "conversations": ["g-1"]}),
            ("reader", {"conversation_id": "other"}),
            ("reader", {}),
            ("reader", {"conversation_id": 7}),
            ("reader", {"conversations": ["g-1"]}),
            ("writer", {"conversation_id": "g-1"}),
            ("writer", {"conversation_id": "g-9"}),
            ("stranger", {"conversation_id": "g-1"}),
            ("stranger", {"conversation_id": "g-9"}),
        ]:
            reply = post(server, body, token=token)
            assert (reply.status, compact(reply.body)) == (403, denied), (token, body)
        # A continuation confers nothing: the reader's own, for g-1, is
        # refused to a client that may not read g-1, before it is decoded.
        first = post(
            server, {"conversation_id": "g-1", "page_bytes": 1}, token="reader"
        )
        token_of_g1 = first.body["continuation"]
        assert isinstance(token_of_g1, str)
        carried = {
            "conversation_id": "g-1",
            "page_bytes": 1,
            "continuation": token_of_g1,
        }
        for token in ["writer", "stranger"]:
            reply = post(server, carried, token=token)
            assert (reply.status, compact(reply.body)) == (403, denied)
        resumed = post(server, carried, token="reader")
        assert resumed.status == 200 and resumed.body["records"][0]["seq"] == 1
