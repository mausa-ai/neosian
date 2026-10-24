"""Grammar checks run through every affected route, before any mutation."""

from typing import Any

import pytest

from tests.contract.storage.support import Storage, message, refused


@pytest.mark.parametrize(
    "scope",
    [
        "",
        "user",
        "user:",
        "User:1",
        "user:a:b",
        "user:1\n",
        "user:..",
        "user:" + "a" * 129,
        "/".join(f"t{n}:a" for n in range(9)),
        "user:" + "a" * 508,
    ],
)
def test_invalid_scope_precedes_path_validation(storage: Storage, scope: str) -> None:
    for route, body in [
        ("read", {"path": "doc"}),
        ("write", {"path": "bad//path", "content": "x"}),
        ("delete", {"path": "doc"}),
        ("rename", {"src": "a", "dst": "b"}),
        ("list_documents", {}),
        ("versions", {"path": "doc"}),
        ("redact", {}),
        ("history", {}),
        ("redactions", {}),
    ]:
        refused(
            storage.server.request("/v1/memory/" + route, {"scope": scope, **body}),
            "memory_scope_invalid",
        )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/a",
        "a/",
        "a//b",
        ".",
        "..",
        "a/../b",
        "a\\b",
        "a b",
        "a" * 129,
        "/".join("s" for _ in range(17)),
        "/".join("a" * 64 for _ in range(9)),
    ],
)
def test_invalid_paths_are_refused(storage: Storage, path: str) -> None:
    for route, body in [
        ("read", {"path": path}),
        ("write", {"path": path, "content": "x"}),
        ("delete", {"path": path}),
        ("versions", {"path": path}),
        ("redact", {"path": path}),
        ("rename", {"src": path, "dst": "ok"}),
        ("rename", {"src": "ok", "dst": path}),
    ]:
        refused(
            storage.server.request(
                "/v1/memory/" + route, {"scope": storage.scope, **body}
            ),
            "memory_path_invalid",
        )
    assert storage.memory("list_documents")["entries"] == []


@pytest.mark.parametrize(
    "conversation", ["", ".", "..", "a/b", "user:1", "a b", "x" * 129, "a\n"]
)
def test_invalid_conversation_ids_are_refused(
    storage: Storage, conversation: str
) -> None:
    cases: list[tuple[str, dict[str, Any]]] = [
        ("append_turn", {"messages": [message("x")]}),
        ("read_turns", {}),
        ("last_turn_number", {}),
        ("append_projections", {"entries": []}),
        ("read_projections", {}),
    ]
    for route, body in cases:
        refused(
            storage.server.request(
                "/v1/conversation/" + route, {"conversation_id": conversation, **body}
            ),
            "agent_conversation_id_invalid",
        )
    refused(
        storage.server.request(
            "/v1/conversation/search_turns",
            {"query": "x", "conversations": [conversation]},
        ),
        "agent_conversation_id_invalid",
    )
