"""Optional governance filtering, independently exercised on every HTTP backend."""

from __future__ import annotations

import pytest

from tests.contract.governance_support import changed, governed
from tests.contract.storage.support import Storage, refused


@pytest.fixture(autouse=True)
def policy_capability(storage: Storage) -> None:
    capability = storage.capabilities.get("knowledge_policy")
    if capability is None:
        pytest.skip("service does not declare optional knowledge-policy version 1")
    assert capability == {"version": 1}
    assert storage.capabilities.get("memory_search") == {"version": 1}


@pytest.mark.parametrize(
    ("state", "effective", "approved"),
    [
        ("unmanaged", True, False),
        ("approved", True, True),
        ("draft", False, False),
        ("superseded", False, False),
        ("expired", False, False),
        ("scheduled", False, False),
        ("future_review", False, False),
        ("stale", False, False),
        ("renamed", False, False),
        ("invalid", False, False),
        ("future_format", False, False),
    ],
)
def test_policy_filters_before_matching_and_excerpts(
    storage: Storage, state: str, effective: bool, approved: bool
) -> None:
    text = governed(storage.scope, "notes/a")
    if state == "unmanaged":
        text = "needle"
    elif state in ("draft", "superseded"):
        text = governed(storage.scope, "notes/a", state=state)
    elif state == "expired":
        text = governed(storage.scope, "notes/a", expires_at="2000-01-02T00:00:00Z")
    elif state == "scheduled":
        text = governed(storage.scope, "notes/a", effective_at="9999-01-01T00:00:00Z")
    elif state == "future_review":
        text = governed(
            storage.scope,
            "notes/a",
            review={"by": "reviewer", "at": "9999-01-01T00:00:00Z", "sha256": ""},
        )
    elif state == "stale":
        text += " changed"
    elif state == "renamed":
        text = governed(storage.scope, "other")
    elif state == "invalid":
        text = changed(text, owners=[])
    elif state == "future_format":
        text = changed(text, format=2)
    storage.memory("write", path="notes/a", content=text)
    for policy, expected in (
        ("all", True),
        ("effective", effective),
        ("approved", approved),
    ):
        page = storage.memory("search", query="needle", knowledge_policy=policy)
        assert len(page["hits"]) == int(expected)
        assert page["next_cursor"] is None
        if not expected:
            assert "needle" not in str(page)


def test_policy_cursors_recheck_current_state_and_bind_policy(storage: Storage) -> None:
    for path, state in (
        ("notes/a", "approved"),
        ("notes/b", "draft"),
        ("notes/z", "approved"),
    ):
        storage.memory(
            "write", path=path, content=governed(storage.scope, path, state=state)
        )
    page = storage.memory(
        "search", query="needle", knowledge_policy="approved", limit=1
    )
    assert [h["path"] for h in page["hits"]] == ["notes/a"]
    cursor = page["next_cursor"]
    assert cursor
    refused(
        storage.server.request(
            "/v1/memory/search",
            {
                "scope": storage.scope,
                "query": "needle",
                "knowledge_policy": "all",
                "cursor": cursor,
            },
        ),
        "value_error",
    )
    storage.peer.post("memory/redact", scope=storage.scope, path="notes/z")
    assert storage.memory(
        "search", query="needle", knowledge_policy="approved", cursor=cursor
    ) == {
        "hits": [],
        "next_cursor": None,
    }
    # An unavailable draft never supplies a lookahead cursor.
    assert (
        storage.memory("search", query="needle", knowledge_policy="approved", limit=1)[
            "next_cursor"
        ]
        is None
    )


@pytest.mark.parametrize("policy", [None, True, [], "historical", 1])
def test_unknown_or_mistyped_policy_refuses(storage: Storage, policy: object) -> None:
    refused(
        storage.server.request(
            "/v1/memory/search",
            {
                "scope": storage.scope,
                "query": "needle",
                "knowledge_policy": policy,
            },
        ),
        "value_error",
    )
