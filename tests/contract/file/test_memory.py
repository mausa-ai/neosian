"""Unreadable memory is refused; mutations cannot disguise a broken journal."""

from typing import Any

import pytest

from tests.contract.file.support import (
    DOCUMENT,
    MEMORY,
    SCOPE,
    VERSIONS,
    append_row,
    snapshot,
)
from tests.contract.storage.support import refused
from tests.contract.support import Binary, serve


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        ("---\n", "", "missing envelope fence"),
        ("neosian_format: 1", "neosian_format: 2", "newer than"),
        ("neosian_format: 1", "neosian_format: true", "missing neosian_format"),
        ("version: 2", "version: 0", "invalid version"),
        ("version: 2", "version: 2.0", "invalid version"),
        ("version: 2", "version: true", "invalid version"),
        (
            "created_at: '2026-08-19T10:00:00Z'",
            "created_at: '2026-08-19T10:00:00'",
            "naive created_at",
        ),
        (
            "updated_at: '2026-08-19T10:00:00Z'",
            "updated_at: 'later'",
            "unreadable updated_at",
        ),
        ("actor: cli:fixture", "actor: true", "actor must be a string"),
        ("version: 2", "version: 2\nredacted: null", "redacted must be a boolean"),
    ],
)
def test_broken_envelope_refuses_reads_and_writes_without_replacing_data(
    planted: Binary, before: str, after: str, reason: str
) -> None:
    path = planted.home / DOCUMENT
    text = path.read_text(encoding="utf-8")
    assert before in text
    with serve(planted) as server:
        path.write_text(text.replace(before, after, 1), encoding="utf-8")
        original = snapshot(planted.home)
        for route, args in [
            ("read", {"path": "notes/fact"}),
            ("list_documents", {}),
            ("write", {"path": "notes/fact", "content": "lost"}),
            ("delete", {"path": "notes/fact"}),
            ("rename", {"src": "notes/fact", "dst": "moved"}),
        ]:
            reply = server.request("/v1/memory/" + route, {"scope": SCOPE, **args})
            details = refused(reply, "memory_format_unsupported")
            assert reason in details["reason"]
            assert snapshot(planted.home) == original


@pytest.mark.parametrize(
    "change",
    [
        "{broken",
        "",
        "[]",
        "{}",
        {"neosian_format": 2},
        {"neosian_format": True},
        {"version": 0},
        {"version": 1.5},
        {"version": True},
        {"action": "invented"},
        {"actor": 7},
        {"redacted": None},
        {"created_at": "2026-08-19T10:00:00"},
        {"created_at": "later"},
    ],
)
def test_broken_sidecar_is_never_skipped_or_overwritten(
    planted: Binary, change: dict[str, Any] | str
) -> None:
    number = append_row(planted.home / VERSIONS, change)
    original = snapshot(planted.home)
    with serve(planted) as server:
        calls: list[tuple[str, dict[str, Any]]] = [
            ("versions", {"path": "notes/fact", "limit": 1}),
            ("history", {"limit": 1}),
            ("write", {"path": "notes/fact", "content": "lost"}),
            ("delete", {"path": "notes/fact"}),
            ("rename", {"src": "notes/fact", "dst": "moved"}),
            ("redact", {"path": "notes/fact"}),
        ]
        for route, args in calls:
            details = refused(
                server.request("/v1/memory/" + route, {"scope": SCOPE, **args}),
                "memory_format_unsupported",
            )
            assert f"sidecar line {number}" in details["reason"]
            assert snapshot(planted.home) == original


@pytest.mark.parametrize(
    "change", ["{broken", "[]", {"ts": "later"}, {"count": -1}, {"actor": 7}]
)
def test_bad_memory_erasure_trail_is_refused(
    planted: Binary, change: dict[str, Any] | str
) -> None:
    number = append_row(planted.home / MEMORY / "redactions.jsonl", change)
    original = snapshot(planted.home)
    with serve(planted) as server:
        details = refused(
            server.request("/v1/memory/redactions", {"scope": SCOPE, "limit": 1}),
            "memory_format_unsupported",
        )
        assert f"line {number}" in details["reason"]
        assert snapshot(planted.home) == original


def test_unreadable_destination_history_prevents_the_entire_rename(
    planted: Binary,
) -> None:
    sidecar = planted.home / MEMORY / "versions/moved.jsonl"
    sidecar.write_text("{broken\n", encoding="utf-8")
    original = snapshot(planted.home)
    with serve(planted) as server:
        refused(
            server.request(
                "/v1/memory/rename",
                {"scope": SCOPE, "src": "notes/fact", "dst": "moved"},
            ),
            "memory_format_unsupported",
        )
        assert snapshot(planted.home) == original


@pytest.mark.parametrize("sidecar_ahead", [False, True])
def test_next_version_is_after_both_the_document_and_the_journal(
    planted: Binary, sidecar_ahead: bool
) -> None:
    document = planted.home / DOCUMENT
    sidecar = planted.home / VERSIONS
    if sidecar_ahead:
        # A crash can leave the synced journal ahead of its document.
        append_row(sidecar, {"version": 7})
    else:
        # A hand-planted document may have no journal at all.
        document.write_text(document.read_text().replace("version: 2", "version: 7"))
        sidecar.unlink()
    with serve(planted) as server:
        updated = server.post(
            "memory/write", scope=SCOPE, path="notes/fact", content="next"
        )["document"]
        assert updated["version"] == 8
        assert (
            server.post("memory/versions", scope=SCOPE, path="notes/fact")["versions"][
                0
            ]["version"]
            == 8
        )
