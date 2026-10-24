"""Continuation and restore boundaries that clients must preserve across versions."""

from __future__ import annotations

import pytest

from tests.contract.support import Server, fixture


def test_memory_cursors_are_opaque_and_method_specific(server: Server) -> None:
    for path in ["a", "b", "c"]:
        server.post("memory/write", scope="user:contract", path=path, content=path)
    first = server.post("memory/list_documents", scope="user:contract", limit=2)
    assert [entry["path"] for entry in first["entries"]] == ["a", "b"]
    cursor = first["next_cursor"]
    assert isinstance(cursor, str) and cursor
    last = server.post(
        "memory/list_documents", scope="user:contract", cursor=cursor, limit=2
    )
    assert [entry["path"] for entry in last["entries"]] == ["c"]
    assert last["next_cursor"] is None
    wrong = server.request(
        "/v1/memory/versions",
        {"scope": "user:contract", "path": "a", "cursor": cursor},
    )
    assert wrong.status == 400
    assert wrong.body["error"]["code"] == "value_error"
    assert wrong.body["error"]["message"].startswith("malformed versions cursor:")


def test_conversation_pages_and_explicit_total_bounds(server: Server) -> None:
    original = fixture("records.json")["turns"][0]
    turns = [{**original, "turn": n} for n in range(1, 502)]
    # A turn can hold many projection entries. Paging must not strand any of them.
    projections = [
        {"turn": 1, "kind": "log", "text": str(n), "span": 1} for n in range(501)
    ]
    server.post(
        "store/restore_conversation",
        conversation_id="contract-session",
        turns=turns,
        projections=projections,
    )
    first = server.post("conversation/read_turns", conversation_id="contract-session")
    assert first["turns"] == turns[:500]
    assert first["next_after"] == 500
    last = server.post(
        "conversation/read_turns", conversation_id="contract-session", after=500
    )
    assert last == {"turns": turns[500:], "next_after": None}
    limited = server.post(
        "conversation/read_turns", conversation_id="contract-session", limit=2
    )
    assert limited == {"turns": turns[:2], "next_after": None}
    assert server.post(
        "conversation/read_projections", conversation_id="contract-session"
    ) == {"entries": projections, "next_after": None}
    assert server.post(
        "conversation/read_projections", conversation_id="contract-session", limit=2
    ) == {"entries": projections[:2], "next_after": None}


@pytest.mark.parametrize("kind", ["scope", "conversation"])
def test_restore_refuses_an_occupied_target(server: Server, kind: str) -> None:
    data = fixture("records.json")
    if kind == "scope":
        body = {
            "scope": "user:contract",
            "documents": [data["document"]],
            "versions": data["versions"],
            "redactions": [data["redaction"]],
        }
    else:
        body = {
            "conversation_id": "contract-session",
            "turns": data["turns"],
            "projections": data["projections"],
        }
    route = "store/restore_" + kind
    server.post(route, **body)
    refused = server.request("/v1/" + route, body)
    assert refused.status == 400
    code = "memory_conflict" if kind == "scope" else "agent_conversation_conflict"
    assert refused.body["error"]["code"] == code
    assert refused.body["error"]["details"]["reason"] == "target_occupied"
    if kind == "scope":
        assert server.post("memory/read", scope="user:contract", path="notes/fact") == {
            "document": data["document"]
        }
    else:
        assert server.post(
            "conversation/read_turns", conversation_id="contract-session"
        ) == {"turns": data["turns"], "next_after": None}
