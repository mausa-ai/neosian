"""Wire 6, all 21 operations, pinned by independently written expectations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from tests.contract.schema import validate, validate_response
from tests.contract.support import LIMITED, Server, fixture, package_version


def restore_scope(server: Server) -> dict[str, Any]:
    data: dict[str, Any] = fixture("records.json")
    assert (
        server.post(
            "store/restore_scope",
            scope="user:contract",
            documents=[data["document"]],
            versions=data["versions"],
            redactions=[data["redaction"]],
        )
        == {}
    )
    return data


def restore_turns(server: Server) -> dict[str, Any]:
    data: dict[str, Any] = fixture("records.json")
    assert (
        server.post(
            "store/restore_conversation",
            conversation_id="contract-session",
            turns=data["turns"],
            projections=data["projections"],
            redactions=[data["turn_redaction"]],
        )
        == {}
    )
    return data


def test_handshake_and_transport_failures(server: Server) -> None:
    health = server.request("/health", method="GET", authenticated=False)
    assert (health.status, health.body) == (200, {"status": "ok"})
    capabilities = server.request("/v1/capabilities", method="GET")
    assert capabilities.body == {
        "wire_version": 6,
        "neosian_version": package_version(),
        "backend": "FileStore",
        "supports_optimistic_concurrency": True,
        "pageable": True,
        "erasable": True,
        "client": "client:contract",
        "memory_search": {"version": 1},
        "change_feed": {"version": 1},
        "knowledge_policy": {"version": 1},
    }
    denied = server.request("/v1/capabilities", method="GET", authenticated=False)
    assert (denied.status, denied.body, denied.headers["www-authenticate"]) == (
        401,
        "unauthorized",
        "Bearer",
    )
    for path, method, status, text in [
        ("/missing", "GET", 404, "Not Found"),
        ("/v1/memory/read", "GET", 405, "Method Not Allowed"),
    ]:
        response = server.request(path, method=method)
        assert (response.status, response.body) == (status, text)


def test_memory_lifecycle_and_version_fence(server: Server) -> None:
    data = restore_scope(server)
    assert server.post("store/scopes") == {"scopes": ["user:contract"]}
    assert server.post("memory/read", scope="user:contract", path="notes/fact") == {
        "document": data["document"]
    }
    assert server.post("memory/read", scope="user:contract", path="missing") == {
        "document": None
    }
    versions = server.post("memory/versions", scope="user:contract", path="notes/fact")
    assert versions == {
        "versions": list(reversed(data["versions"][:2])),
        "next_cursor": None,
    }
    assert server.post("memory/list_documents", scope="user:contract", limit=0) == {
        "entries": [],
        "next_cursor": None,
    }
    entry = {
        key: data["document"][key]
        for key in ("path", "version", "created_at", "updated_at", "redacted")
    }
    assert server.post("memory/list_documents", scope="user:contract") == {
        "entries": [entry],
        "next_cursor": None,
    }
    changed = server.post(
        "memory/write",
        scope="user:contract",
        path="notes/fact",
        content="changed",
        expected_version=2,
        actor="writer",
    )["document"]
    validate("Document", changed)
    assert changed == {
        **data["document"],
        "content": "changed",
        "version": 3,
        "actor": "client:contract/writer",
        "updated_at": changed["updated_at"],
    }
    assert datetime.fromisoformat(changed["updated_at"]) >= datetime.fromisoformat(
        changed["created_at"]
    )
    conflict = server.request(
        "/v1/memory/write",
        {
            "scope": "user:contract",
            "path": "notes/fact",
            "content": "lost",
            "expected_version": 2,
        },
    )
    assert conflict.status == 400
    assert conflict.body == {
        "error": {
            "code": "memory_conflict",
            "message": "Memory conflict on 'notes/fact' in scope 'user:contract': version_mismatch",
            "details": {
                "scope": "user:contract",
                "path": "notes/fact",
                "reason": "version_mismatch",
                "expected_version": 2,
                "actual_version": 3,
            },
        }
    }
    renamed = server.post(
        "memory/rename", scope="user:contract", src="notes/fact", dst="notes/moved"
    )["document"]
    assert renamed["content"] == "changed" and renamed["path"] == "notes/moved"
    assert server.post("memory/read", scope="user:contract", path="notes/fact") == {
        "document": None
    }
    assert server.post("memory/delete", scope="user:contract", path="notes/moved") == {
        "deleted": True
    }
    assert server.post("memory/delete", scope="user:contract", path="notes/moved") == {
        "deleted": False
    }
    history = server.post("memory/history", scope="user:contract")
    validate_response("memory/history", history)
    assert history["versions"][0]["action"] == "deleted"


def test_memory_erasure_keeps_version_and_timestamp(server: Server) -> None:
    data = restore_scope(server)
    assert server.post(
        "memory/redact", scope="user:contract", path="notes/fact", actor="erase"
    ) == {"count": 1}
    assert server.post("memory/read", scope="user:contract", path="notes/fact") == {
        "document": {**data["document"], "content": "", "redacted": True}
    }
    versions = server.post("memory/versions", scope="user:contract", path="notes/fact")[
        "versions"
    ]
    assert versions == [
        {**v, "content": "", "redacted": True} for v in reversed(data["versions"][:2])
    ]
    trail = server.post("memory/redactions", scope="user:contract")
    validate_response("memory/redactions", trail)
    assert trail["redactions"][0] == {
        "path": "notes/fact",
        "actor": "client:contract/erase",
        "created_at": trail["redactions"][0]["created_at"],
        "count": 1,
    }
    assert trail["redactions"][1] == data["redaction"]


def test_turns_projections_search_and_erasure(server: Server) -> None:
    data = restore_turns(server)
    assert server.post("store/conversations") == {"conversations": ["contract-session"]}
    assert server.post(
        "conversation/last_turn_number", conversation_id="contract-session"
    ) == {"turn": 2}
    assert server.post(
        "conversation/read_turns", conversation_id="contract-session"
    ) == {"turns": data["turns"], "next_after": None}
    assert server.post(
        "conversation/read_projections", conversation_id="contract-session"
    ) == {"entries": data["projections"], "next_after": None}
    assert server.post("conversation/search_turns", query="CAFÉ memory") == {
        "turns": [data["turns"][0]]
    }
    assert server.post("conversation/search_turns", query="café", conversations=[]) == {
        "turns": []
    }
    appended = server.post(
        "conversation/append_turn",
        conversation_id="contract-session",
        messages=[{"role": "user", "content": "new evidence"}],
        actor="writer",
    )["turn"]
    assert appended == {
        "conversation_id": "contract-session",
        "turn": 3,
        "messages": [
            {
                "role": "user",
                "content": "new evidence",
                "reasoning": None,
                "tool_calls": [],
                "tool_call_id": None,
            }
        ],
        "created_at": appended["created_at"],
        "actor": "client:contract/writer",
        "redacted": False,
    }
    validate("Turn", appended)
    assert (
        server.post(
            "conversation/append_projections",
            conversation_id="contract-session",
            entries=[{"turn": 3, "kind": "log", "text": "new log"}],
        )
        == {}
    )
    assert server.post(
        "conversation/read_projections", conversation_id="contract-session", after=2
    ) == {
        "entries": [{"turn": 3, "kind": "log", "text": "new log", "span": 1}],
        "next_after": None,
    }
    assert server.post(
        "conversation/redact_turns",
        conversation_id="contract-session",
        turns=[1],
        actor="erase",
    ) == {"count": 1}
    assert server.post("conversation/search_turns", query="café") == {"turns": []}
    erased = server.post(
        "conversation/read_turns", conversation_id="contract-session", limit=1
    )["turns"]
    assert erased == [{**data["turns"][0], "messages": [], "redacted": True}]
    projections = server.post(
        "conversation/read_projections", conversation_id="contract-session"
    )["entries"]
    assert all(
        "reviewed memory" not in p["text"] and "recorded summary" not in p["text"]
        for p in projections
    )
    trail = server.post(
        "conversation/turn_redactions", conversations=["contract-session"]
    )
    validate_response("conversation/turn_redactions", trail)
    assert trail["redactions"][0]["turns"] == [1]
    assert trail["redactions"][1] == data["turn_redaction"]


@pytest.mark.parametrize(
    "route,body,message",
    [
        ("memory/read", {}, "missing parameter: 'scope'"),
        (
            "memory/write",
            {"scope": "user:a", "path": "p", "content": True},
            "parameter 'content' must be a string",
        ),
        (
            "memory/versions",
            {"scope": "user:a", "path": "p", "limit": True},
            "parameter 'limit' must be an integer",
        ),
        (
            "memory/history",
            {"scope": "user:a", "since": "2026-08-19T10:00:00"},
            "naive wire timestamp refused: '2026-08-19T10:00:00'",
        ),
        (
            "conversation/append_turn",
            {"conversation_id": "c", "messages": [{}]},
            "malformed request: 'role'",
        ),
        (
            "conversation/search_turns",
            {"query": "x", "limit": 501},
            "a search answers at most 500 turns; narrow the query",
        ),
    ],
)
def test_typed_refusals(server: Server, route: str, body: Any, message: str) -> None:
    reply = server.request("/v1/" + route, body)
    assert (reply.status, reply.body) == (
        400,
        {"error": {"code": "value_error", "message": message, "details": {}}},
    )


@pytest.mark.parametrize(
    "route,body",
    [
        ("memory/read", {"scope": "user:other", "path": "secret"}),
        ("conversation/read_turns", {"conversation_id": "other"}),
        ("conversation/search_turns", {"query": "secret"}),
        ("conversation/search_turns", {"query": "secret", "conversations": ["other"]}),
        ("conversation/turn_redactions", {}),
        ("store/scopes", {}),
        ("store/conversations", {}),
        ("store/restore_scope", {}),
        ("store/restore_conversation", {}),
    ],
)
def test_allowances_precede_reads_and_restore(
    server: Server, route: str, body: Any
) -> None:
    reply = server.request("/v1/" + route, body, token=LIMITED)
    assert reply.status == 403
    validate("Error", reply.body)
    assert reply.body["error"]["code"] == "forbidden"
    assert server.request(
        "/v1/memory/read", {"scope": "user:allowed", "path": "p"}, token=LIMITED
    ).body == {"document": None}
