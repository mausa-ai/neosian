"""Optional memory-search v1: literal expectations shared by independent backends."""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.storage.support import Storage, refused


@pytest.fixture(autouse=True)
def search_capability(storage: Storage) -> None:
    capability = storage.capabilities.get("memory_search")
    if capability is None:
        pytest.skip("service does not declare optional memory-search version 1")
    assert capability == {"version": 1}


@pytest.mark.parametrize(
    ("content", "query", "matches"),
    [
        ("Ship the BLUE release", "blue SHIP", True),
        ("Ship the BLUE release", "ship missing", False),
        ("abcde", "BCD bcd", True),
        ("a.b [x]", "a.b [x]", True),
        ("axb", "a.b", False),
        ("CAFÉ", "café", True),
        ("café", "cafe", False),
        ("café", "cafe\u0301", False),
        ("Straße", "STRASSE", False),
        ("ΟΣ", "οσ", True),
        ("İSTANBUL", "i\u0307stanbul", True),
        ("東京の決定 🚀", "東京 🚀", True),
        ("blue\nrelease", "blue\u2003release", True),
    ],
)
def test_lexical_matching_is_explicit_and_backend_independent(
    storage: Storage, content: str, query: str, matches: bool
) -> None:
    doc = storage.memory("write", path="doc", content=content, actor="writer")[
        "document"
    ]
    page = storage.memory("search", query=query, fields=["content"])
    assert page["next_cursor"] is None
    assert len(page["hits"]) == int(matches)
    if matches:
        assert page["hits"] == [
            {
                key: doc[key]
                for key in (
                    "scope",
                    "path",
                    "version",
                    "created_at",
                    "updated_at",
                    "actor",
                )
            }
            | {
                "matched_fields": ["content"],
                "excerpt": {
                    "field": "content",
                    "metadata_key": None,
                    "offset": 0,
                    "text": content,
                },
            }
        ]


def test_paths_prefixes_scope_and_lifecycle_exclusions(storage: Storage) -> None:
    for path in [
        "notes/z",
        "notes/a",
        "notebook",
        "skills/review",
        "sessions/old",
        "messages/msg",
        "message-links/link",
        "handoff",
        "erased",
        "deleted",
    ]:
        storage.memory("write", path=path, content="needle")
    storage.memory("redact", path="erased")
    storage.memory("delete", path="deleted")
    storage.server.post(
        "memory/write", scope=storage.scope + "-other", path="foreign", content="needle"
    )
    page = storage.memory("search", query="needle")
    assert [h["path"] for h in page["hits"]] == [
        "notebook",
        "notes/a",
        "notes/z",
        "skills/review",
    ]
    assert all(h["scope"] == storage.scope for h in page["hits"])
    assert [
        h["path"]
        for h in storage.memory("search", query="needle", prefix="notes/")["hits"]
    ] == ["notes/a", "notes/z"]
    assert (
        storage.memory("search", query="erased deleted", fields=["path"])["hits"] == []
    )
    assert storage.memory("search", query="review", fields=["path"])["hits"][0][
        "excerpt"
    ] == {"field": "path", "metadata_key": None, "offset": 0, "text": "skills/review"}
    assert storage.memory("search", query="needle", prefix="notes/%")["hits"] == []


def test_excerpts_use_original_unicode_characters(storage: Storage) -> None:
    content = "é" * 300 + "İSTANBUL" + "tail " * 100
    storage.memory("write", path="doc", content=content)
    excerpt = storage.memory("search", query="i\u0307stanbul")["hits"][0]["excerpt"]
    assert excerpt == {
        "field": "content",
        "metadata_key": None,
        "offset": 260,
        "text": content[260:500],
    }


def test_only_explicit_metadata_string_values_participate(storage: Storage) -> None:
    storage.require("portable")
    document = {
        "scope": storage.scope,
        "path": "notes/release",
        "content": "Ship safely",
        "version": 1,
        "created_at": "2026-10-24T10:00:00Z",
        "updated_at": "2026-10-24T10:00:00Z",
        "actor": "import:fixture",
        "redacted": False,
        "extra": {"tags": {"object-key": [42, True, "Blue"]}, "secret": "private"},
    }
    version = {
        k: document[k]
        for k in ("path", "content", "version", "created_at", "actor", "redacted")
    } | {"action": "created"}
    storage.server.post(
        "store/restore_scope",
        scope=storage.scope,
        documents=[document],
        versions=[version],
        redactions=[],
    )
    assert storage.memory("search", query="blue")["hits"] == []
    options = {"fields": ["path", "content", "metadata"], "metadata_keys": ["tags"]}
    hit = storage.memory("search", query="release SHIP blue", **options)["hits"][0]
    assert hit["actor"] == "import:fixture" and hit["version"] == 1
    assert hit["matched_fields"] == ["content", "metadata", "path"]
    for query in ["object-key", "42", "true", "private"]:
        assert storage.memory("search", query=query, **options)["hits"] == []
    hit = storage.memory(
        "search", query="blue", fields=["metadata"], metadata_keys=["tags"]
    )["hits"][0]
    assert hit["excerpt"] == {
        "field": "metadata",
        "metadata_key": "tags",
        "offset": 0,
        "text": "Blue",
    }
    storage.memory("redact", path="notes/release")
    assert storage.memory("search", query="blue", **options)["hits"] == []


def test_pages_read_current_data_and_bind_the_query(storage: Storage) -> None:
    for path in ["d", "c", "b", "a"]:
        storage.memory("write", path=path, content="needle")
    first = storage.memory("search", query="needle", limit=1)
    assert [h["path"] for h in first["hits"]] == ["a"]
    cursor = first["next_cursor"]
    assert isinstance(cursor, str) and cursor
    variations: list[dict[str, Any]] = [
        {"query": "other"},
        {"prefix": "b"},
        {"fields": ["content"]},
        {"scope": storage.scope + "-other"},
        {"fields": ["metadata"], "metadata_keys": ["tags"]},
    ]
    for changes in variations:
        refused(
            storage.server.request(
                "/v1/memory/search",
                {
                    "scope": storage.scope,
                    "query": "needle",
                    "cursor": cursor,
                    **changes,
                },
            ),
            "value_error",
        )
    storage.peer.post("memory/delete", scope=storage.scope, path="b")
    storage.peer.post("memory/redact", scope=storage.scope, path="c")
    storage.peer.post("memory/write", scope=storage.scope, path="d", content="changed")
    for path in ["0", "aa", "z"]:
        storage.memory("write", path=path, content="needle")
    second = storage.memory("search", query="needle", limit=2, cursor=cursor)
    assert [h["path"] for h in second["hits"]] == ["aa", "z"]
    assert second["next_cursor"] is None
    assert [h["path"] for h in storage.memory("search", query="needle")["hits"]] == [
        "0",
        "a",
        "aa",
        "z",
    ]
    storage.memory("rename", src="z", dst="last")
    assert [h["path"] for h in storage.memory("search", query="needle")["hits"]][
        -1
    ] == "last"


@pytest.mark.parametrize(
    "bad",
    [
        {"query": ""},
        {"query": " \t\n"},
        {"query": "x" * 1025},
        {"query": " ".join(str(i) for i in range(17))},
        {"query": 2},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"limit": "1"},
        {"limit": None},
        {"fields": []},
        {"fields": ["unknown"]},
        {"fields": ["path", "path"]},
        {"fields": None},
        {"fields": [2]},
        {"metadata_keys": ["tags"]},
        {"fields": ["metadata"]},
        {"fields": ["metadata"], "metadata_keys": ["tags", "tags"]},
        {"fields": ["metadata"], "metadata_keys": [""]},
        {"cursor": "not-a-cursor"},
        {"cursor": ""},
        {"cursor": 2},
        {"prefix": None},
        {"prefix": "a" * 1025},
        {"unknown": "ignored?"},
    ],
)
def test_invalid_search_requests_refuse(storage: Storage, bad: dict[str, Any]) -> None:
    refused(
        storage.server.request(
            "/v1/memory/search", {"scope": storage.scope, "query": "needle", **bad}
        ),
        "value_error",
    )
