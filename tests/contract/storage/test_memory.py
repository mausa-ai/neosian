"""Memory, ledgers and serial version fences through an independent HTTP client."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.contract.storage.support import Storage, refused


@pytest.mark.parametrize(
    "content", ["", "x", "x\n", "\n", "a\r\nb", "---\ntitle: t\n---\nbody", "café ✓"]
)
def test_content_and_attribution_round_trip(storage: Storage, content: str) -> None:
    assert storage.memory("list_documents")["entries"] == []
    assert storage.memory("read", path="doc") == {"document": None}
    first = storage.memory("write", path="doc", content=content, actor="writer")[
        "document"
    ]
    assert first == {
        "scope": storage.scope,
        "path": "doc",
        "content": content,
        "version": 1,
        "created_at": first["created_at"],
        "updated_at": first["updated_at"],
        "actor": storage.client + "/writer",
        "redacted": False,
        "extra": {},
    }
    assert storage.memory("read", path="doc") == {"document": first}
    assert first["created_at"] == first["updated_at"]
    assert datetime.fromisoformat(first["created_at"]).utcoffset() == timedelta(0)
    second = storage.memory("write", path="doc", content=content)["document"]
    assert second["version"] == 2 and second["actor"] == storage.client
    assert second["created_at"] == first["created_at"]
    assert datetime.fromisoformat(second["updated_at"]) >= datetime.fromisoformat(
        first["updated_at"]
    )
    rows = storage.memory("versions", path="doc")["versions"]
    assert [(r["version"], r["action"], r["content"]) for r in rows] == [
        (2, "modified", content),
        (1, "created", content),
    ]


def test_delete_recreate_and_rename_keep_independent_histories(
    storage: Storage,
) -> None:
    assert storage.memory("delete", path="missing") == {"deleted": False}
    assert storage.memory("versions", path="missing")["versions"] == []
    storage.memory("write", path="source", content="first")
    storage.memory("write", path="source", content="second")
    assert storage.memory("delete", path="source", actor="delete") == {"deleted": True}
    assert storage.memory("read", path="source") == {"document": None}
    deleted = storage.memory("versions", path="source")["versions"][0]
    assert (deleted["version"], deleted["content"], deleted["actor"]) == (
        3,
        "second",
        storage.client + "/delete",
    )
    assert deleted["action"] == "deleted"
    assert (
        storage.memory("write", path="source", content="again")["document"]["version"]
        == 4
    )
    storage.memory("write", path="dest", content="old destination")
    storage.memory("delete", path="dest")
    moved = storage.memory("rename", src="source", dst="dest", actor="move")["document"]
    assert (moved["path"], moved["content"], moved["version"]) == ("dest", "again", 3)
    assert storage.memory("read", path="source") == {"document": None}
    assert storage.memory("versions", path="source")["versions"][0]["version"] == 5
    assert storage.memory("versions", path="dest")["versions"][0]["action"] == "created"
    for src, dst, code in [
        ("absent", "dest", "memory_document_not_found"),
        ("dest", "dest", "memory_conflict"),
    ]:
        refused(
            storage.server.request(
                "/v1/memory/rename", {"scope": storage.scope, "src": src, "dst": dst}
            ),
            code,
        )
    assert storage.memory("read", path="dest")["document"] == moved
    occupied = storage.memory("write", path="taken", content="keep")["document"]
    refused(
        storage.server.request(
            "/v1/memory/rename", {"scope": storage.scope, "src": "dest", "dst": "taken"}
        ),
        "memory_conflict",
    )
    assert storage.memory("read", path="taken")["document"] == occupied
    assert storage.memory("read", path="dest")["document"] == moved


def test_prefix_order_scope_isolation_and_limits(storage: Storage) -> None:
    for path in ["zebra", "notes/b", "notebook", "notes/a", "gone"]:
        storage.memory("write", path=path, content=path)
    storage.memory("delete", path="gone")
    assert [
        r["path"] for r in storage.memory("list_documents", prefix="note")["entries"]
    ] == ["notebook", "notes/a", "notes/b"]
    assert storage.memory("list_documents", prefix="not a path //")["entries"] == []
    assert storage.memory("list_documents", limit=0)["entries"] == []
    assert storage.server.post(
        "memory/read", scope=storage.scope + "-other", path="notebook"
    ) == {"document": None}
    assert (
        storage.memory("versions", path="gone", limit=1)["versions"][0]["version"] == 2
    )
    assert storage.memory("versions", path="gone", limit=0)["versions"] == []


def test_version_mismatches_are_required_even_without_cross_worker_support(
    storage: Storage,
) -> None:
    for expected in [0, 1]:
        details = refused(
            storage.server.request(
                "/v1/memory/write",
                {
                    "scope": storage.scope,
                    "path": "doc",
                    "content": "lost",
                    "expected_version": expected,
                },
            ),
            "memory_conflict",
        )
        assert details["reason"] == "document_absent"
    first = storage.memory("write", path="doc", content="first")["document"]
    for expected in [0, 2, 99]:
        details = refused(
            storage.server.request(
                "/v1/memory/write",
                {
                    "scope": storage.scope,
                    "path": "doc",
                    "content": "lost",
                    "expected_version": expected,
                },
            ),
            "memory_conflict",
        )
        assert details["reason"] == "version_mismatch"
    assert storage.memory("read", path="doc")["document"] == first
    assert (
        storage.memory("write", path="doc", content="second", expected_version=1)[
            "document"
        ]["version"]
        == 2
    )


def test_memory_erasure_and_recreation_preserve_the_audit_skeleton(
    storage: Storage,
) -> None:
    storage.memory("write", path="alive", content="secret", actor="writer")
    storage.memory("write", path="alive", content="secret again", actor="writer")
    storage.memory("write", path="dead", content="secret", actor="writer")
    storage.memory("delete", path="dead")
    before = storage.memory("read", path="alive")["document"]
    history = storage.memory("history")["versions"]
    assert {(r["path"], r["version"], r["action"]) for r in history} == {
        ("alive", 1, "created"),
        ("alive", 2, "modified"),
        ("dead", 1, "created"),
        ("dead", 2, "deleted"),
    }
    assert storage.memory("redact", path="alive", actor="erase") == {"count": 1}
    assert storage.memory("read", path="alive")["document"] == {
        **before,
        "content": "",
        "redacted": True,
    }
    assert storage.memory("redact", actor="erase") == {"count": 2}
    assert storage.memory("redact", actor="erase") == {"count": 2}
    assert storage.memory("history")["versions"] == [
        {**r, "content": "", "redacted": True} for r in history
    ]
    assert storage.memory("read", path="dead") == {"document": None}
    assert storage.memory("list_documents")["entries"][0]["redacted"] is True
    acts = storage.memory("redactions")["redactions"]
    assert [(a["path"], a["count"], a["actor"]) for a in acts] == [
        (None, 2, storage.client + "/erase"),
        (None, 2, storage.client + "/erase"),
        ("alive", 1, storage.client + "/erase"),
    ]
    assert storage.memory("redactions", limit=1)["redactions"] == acts[:1]
    assert (
        storage.memory("redactions", since=acts[0]["created_at"])["redactions"][0]
        == acts[0]
    )
    assert storage.memory("redact", path="missing") == {"count": 0}
    assert storage.memory("redactions")["redactions"] == acts
    recreated = storage.memory("write", path="alive", content="new life")["document"]
    assert (recreated["version"], recreated["redacted"]) == (3, False)
    assert storage.memory("versions", path="alive")["versions"][-1]["redacted"] is True


def test_ledger_since_is_inclusive_and_unknown_scope_is_empty(storage: Storage) -> None:
    assert storage.memory("history")["versions"] == []
    assert storage.memory("redactions")["redactions"] == []
    for content in ["one", "two", "three"]:
        storage.memory("write", path="doc", content=content)
    rows = storage.memory("history")["versions"]
    assert [r["version"] for r in rows] == [3, 2, 1]
    assert storage.memory("history", limit=1)["versions"] == rows[:1]
    assert (
        storage.memory("history", since=rows[0]["created_at"])["versions"][0] == rows[0]
    )
    for route in ["history", "redactions"]:
        refused(
            storage.server.request(
                "/v1/memory/" + route,
                {"scope": storage.scope, "since": "2026-08-19T12:00:00"},
            ),
            "value_error",
        )
