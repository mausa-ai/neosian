"""Optional change-feed v1 through raw HTTP, shared by independent backends."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.contract.storage.support import Storage, refused


@pytest.fixture(autouse=True)
def capability(storage: Storage) -> None:
    declared = storage.capabilities.get("change_feed")
    if declared is None:
        pytest.skip("service does not declare optional change-feed version 1")
    assert declared == {"version": 1}


def test_mutations_have_stable_references_and_no_payload_copies(
    storage: Storage,
) -> None:
    empty = storage.memory("changes")
    assert empty["gap"] == "untracked" and empty["events"] == []
    assert empty["cursor"] is None
    marker = "content-and-metadata-must-not-be-copied"
    storage.memory("write", path="skills/review", content=marker)
    first = storage.memory("changes", limit=1)
    assert first["has_more"] and first["gap"] is None
    baseline = first["events"][0]
    assert baseline["kind"] == "state.scope_reset"
    assert baseline["reason"] == "tracking_started"
    assert baseline["sequence"] == 1
    storage.memory("write", path="skills/review", content=marker + " changed")
    storage.memory("rename", src="skills/review", dst="knowledge/review")
    storage.memory("delete", path="knowledge/review")
    storage.memory("redact", path="knowledge/review")
    page = storage.memory("changes", cursor=first["cursor"])
    assert page["gap"] is None and not page["has_more"]
    events = page["events"]
    assert [e["kind"] for e in events] == [
        "state.document_created",
        "state.document_modified",
        "state.document_renamed",
        "state.document_deleted",
        "state.document_erased",
    ]
    assert [e["sequence"] for e in events] == [2, 3, 4, 5, 6]
    assert [e["version"] for e in events] == [1, 2, 1, 2, None]
    assert events[2]["previous_path"] == "skills/review"
    assert events[2]["previous_version"] == 3
    for event in [baseline, *events]:
        assert event["id"] == f'{page["stream"]}:{event["sequence"]}'
        assert event["scope"] == storage.scope
        assert event["created_at"].endswith("Z")
        assert set(event) == {
            "format",
            "id",
            "scope",
            "stream",
            "sequence",
            "kind",
            "path",
            "version",
            "previous_path",
            "previous_version",
            "reason",
            "created_at",
        }
    assert marker not in json.dumps(page)
    assert (
        storage.peer.post("memory/changes", scope=storage.scope, cursor=first["cursor"])
        == page
    )
    caught_up = storage.memory("changes", cursor=page["cursor"])
    assert caught_up["events"] == [] and caught_up["cursor"] == page["cursor"]
    storage.memory("write", path="later", content="new")
    assert (
        storage.memory("changes", cursor=page["cursor"])["events"][0]["sequence"] == 7
    )


def test_failed_guards_and_noops_do_not_advance_stream(storage: Storage) -> None:
    storage.memory("write", path="doc", content="one")
    before = storage.memory("changes")
    refused(
        storage.server.request(
            "/v1/memory/write",
            {
                "scope": storage.scope,
                "path": "doc",
                "content": "wrong",
                "expected_version": 42,
            },
        ),
        "memory_conflict",
    )
    storage.memory("delete", path="missing")
    storage.memory("redact", path="missing")
    assert storage.memory("changes") == before


def test_scope_erasure_has_one_reference_without_content(storage: Storage) -> None:
    storage.memory("write", path="a", content="private-a")
    storage.memory("write", path="b", content="private-b")
    before = storage.memory("changes")
    storage.memory("redact")
    page = storage.memory("changes", cursor=before["cursor"])
    assert len(page["events"]) == 1
    assert page["events"][0]["kind"] == "state.scope_erased"
    assert page["events"][0]["path"] is None
    assert page["events"][0]["version"] is None
    assert "private-" not in json.dumps(page)


def test_retention_is_explicit_bounded_and_never_reuses_sequences(
    storage: Storage,
) -> None:
    storage.memory("write", path="a", content="one")
    first = storage.memory("changes", limit=1)
    before = storage.memory("changes")
    preview = storage.memory("prune_changes", through=first["cursor"])
    assert preview["removed"] == 1 and preview["write"] is False
    assert storage.memory("changes") == before
    applied = storage.memory("prune_changes", through=first["cursor"], write=True)
    assert applied == preview | {"write": True}
    gap = storage.memory("changes", cursor=before["earliest_cursor"])
    assert gap["gap"] == "retention" and not gap["has_more"]
    assert gap["events"] == [] and gap["cursor"] == applied["earliest_cursor"]
    assert storage.memory("changes")["gap"] == "retention"
    assert (
        storage.memory("changes", cursor=gap["cursor"])["events"]
        == before["events"][1:]
    )
    storage.memory("prune_changes", through=before["head_cursor"], write=True)
    storage.memory("write", path="b", content="two")
    later = storage.memory("changes", cursor=before["head_cursor"])
    assert later["events"][0]["sequence"] == 3
    assert later["stream"] == before["stream"]


def test_concurrent_workers_publish_contiguous_replayable_pages(
    storage: Storage,
) -> None:
    def write(n: int) -> None:
        endpoint = storage.server if n % 2 else storage.peer
        endpoint.post(
            "memory/write", scope=storage.scope, path=f"doc-{n}", content=str(n)
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(write, range(105)))
    first = storage.memory("changes", limit=100)
    second = storage.memory("changes", limit=100, cursor=first["cursor"])
    assert len(first["events"]) == 100 and first["has_more"]
    assert len(second["events"]) == 6 and not second["has_more"]
    events = first["events"] + second["events"]
    assert [e["sequence"] for e in events] == list(range(1, 107))
    assert len({e["id"] for e in events}) == 106
    assert {e["path"] for e in events[1:]} == {f"doc-{n}" for n in range(105)}
    assert storage.memory("changes", limit=100) == first


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": True},
        {"limit": 1.5},
        {"cursor": ""},
        {"cursor": "x" * 2049},
        {"cursor": 1},
        {"unexpected": "field"},
    ],
)
def test_invalid_requests_refuse_without_mutation(
    storage: Storage, payload: dict[str, Any]
) -> None:
    reply = storage.server.request(
        "/v1/memory/changes", {"scope": storage.scope, **payload}
    )
    assert reply.status == 400
    assert storage.memory("changes")["gap"] == "untracked"


def test_cursors_cannot_cross_scopes(storage: Storage) -> None:
    storage.memory("write", path="doc", content="one")
    cursor = storage.memory("changes")["cursor"]
    reply = storage.server.request(
        "/v1/memory/changes", {"scope": storage.scope + "-other", "cursor": cursor}
    )
    assert reply.status == 400
    reply = storage.server.request(
        "/v1/memory/prune_changes",
        {"scope": storage.scope + "-other", "through": cursor, "write": True},
    )
    assert reply.status == 400


def test_restore_announces_reconciliation_instead_of_forging_old_events(
    storage: Storage,
) -> None:
    storage.require("portable")
    doc = storage.memory("write", path="doc", content="restored")["document"]
    versions = storage.memory("versions", path="doc")["versions"]
    target = storage.scope + "-restored"
    storage.server.post(
        "store/restore_scope",
        scope=target,
        documents=[doc | {"scope": target}],
        versions=[row | {"scope": target} for row in versions],
        redactions=[],
    )
    page = storage.server.post("memory/changes", scope=target)
    assert len(page["events"]) == 1
    assert page["events"][0]["kind"] == "state.scope_reset"
    assert page["events"][0]["reason"] == "restored"
    assert page["stream"] != storage.memory("changes")["stream"]
