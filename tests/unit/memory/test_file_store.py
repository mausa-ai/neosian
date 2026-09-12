"""FileStore-specific tests: on-disk layout, atomicity, containment,
best-effort optimistic concurrency, corruption handling, the redaction
trail. Cross-implementation semantics live in test_file_contract.py.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryFormatUnsupportedError,
    MemoryPathInvalidError,
)
from tests.support.clock import ManualClock

_SCOPE = "user:123/proj:erp"


def _mode(path: Path) -> int:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    return path.stat().st_mode & 0o777


class TestPrivateModes:
    """Ledger #126: everything the store writes is private by decision —
    the sidecar and the redaction trail were 0644 while the document was
    0600 by accident."""

    async def test_files_are_0600_and_directories_0700(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "notes/api", "body")
        await store.redact(_SCOPE, path="notes/api")
        scope_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp"
        for file in (
            scope_dir / "documents" / "notes" / "api.md",
            scope_dir / "versions" / "notes" / "api.jsonl",
            scope_dir / "redactions.jsonl",
        ):
            assert _mode(file) == 0o600, file
        for directory in (
            tmp_path / "memory",
            scope_dir,
            scope_dir / "documents" / "notes",
            scope_dir / "versions" / "notes",
        ):
            assert _mode(directory) == 0o700, directory

    async def test_the_sidecar_keeps_its_mode_across_redaction(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "a", "one")
        await store.write(_SCOPE, "a", "two")
        sidecar = (
            tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "versions" / "a.jsonl"
        )
        before = _mode(sidecar)
        await store.redact(_SCOPE, path="a")
        assert _mode(sidecar) == before == 0o600


class TestOnDiskLayout:
    async def test_write_produces_the_documented_tree(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "notes/api", "body", actor="conv-1")
        scope_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp"
        assert (scope_dir / "documents" / "notes" / "api.md").is_file()
        assert (scope_dir / "versions" / "notes" / "api.jsonl").is_file()

    async def test_document_file_is_envelope_plus_verbatim_body(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "note", "hello\n")
        doc = (
            tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "documents" / "note.md"
        )
        with doc.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        assert text.startswith("---\nneosian_format: 1\n")
        assert text.endswith("---\nhello\n")

    async def test_max_length_scope_works_on_disk(self, store: FileStore) -> None:
        scope = "/".join(["t:" + "x" * 128] * 3 + ["t:" + "x" * 117])
        document = await store.write(scope, "doc", "content")
        assert document.version == 1
        assert (await store.read(scope, "doc")).content == "content"  # type: ignore[union-attr]

    async def test_atomic_writes_leave_no_temp_files(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "a", "x")
        await store.redact(_SCOPE, path="a")
        leftovers = list((tmp_path / "memory").rglob(".neosian-tmp-*"))
        assert leftovers == []

    async def test_read_and_list_on_unwritten_scope_create_nothing(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        assert await store.read("user:ghost", "doc") is None
        assert await store.list_documents("user:ghost") == ()
        assert list((tmp_path / "memory").iterdir()) == []

    async def test_symlink_escape_is_refused(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        docs_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "documents"
        docs_dir.mkdir(parents=True)
        (docs_dir / "evil").symlink_to(outside)
        with pytest.raises(MemoryPathInvalidError) as exc_info:
            await store.write(_SCOPE, "evil/doc", "x")
        assert "escapes" in exc_info.value.reason
        assert list(outside.iterdir()) == []


class TestExpectedVersionBestEffort:
    """FileStore declares supports_optimistic_concurrency=False yet still
    honors expected_version in-process; the contract kit gates these on
    the ClassVar, so the reference behavior is pinned here."""

    async def test_the_classvar_stays_false(self) -> None:
        assert FileStore.supports_optimistic_concurrency is False

    async def test_mismatch_raises_conflict_with_both_versions(
        self, store: FileStore
    ) -> None:
        await store.write(_SCOPE, "doc", "v1")
        with pytest.raises(MemoryConflictError) as exc_info:
            await store.write(_SCOPE, "doc", "v2", expected_version=7)
        error = exc_info.value
        assert error.reason == "version_mismatch"
        assert error.expected_version == 7
        assert error.actual_version == 1

    async def test_absent_document_raises_conflict(self, store: FileStore) -> None:
        with pytest.raises(MemoryConflictError) as exc_info:
            await store.write(_SCOPE, "ghost", "x", expected_version=1)
        assert exc_info.value.reason == "document_absent"

    async def test_matching_version_writes(self, store: FileStore) -> None:
        await store.write(_SCOPE, "doc", "v1")
        document = await store.write(_SCOPE, "doc", "v2", expected_version=1)
        assert document.version == 2


class TestCorruption:
    async def test_malformed_sidecar_line_raises_never_skips(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "doc", "x")
        sidecar = (
            tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "versions" / "doc.jsonl"
        )
        with sidecar.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        with pytest.raises(MemoryFormatUnsupportedError) as exc_info:
            await store.versions(_SCOPE, "doc")
        assert "line 2" in exc_info.value.reason

    async def test_newer_format_sidecar_row_is_refused(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "doc", "x")
        sidecar = (
            tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "versions" / "doc.jsonl"
        )
        row = json.loads(sidecar.read_text().splitlines()[0])
        row["neosian_format"] = 99
        with sidecar.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        with pytest.raises(MemoryFormatUnsupportedError):
            await store.versions(_SCOPE, "doc")

    async def test_foreign_file_in_documents_is_skipped_silently(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "mine", "x")
        docs_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "documents"
        (docs_dir / "My Notes.md").write_text("not ours")
        entries = await store.list_documents(_SCOPE)
        assert [entry.path for entry in entries] == ["mine"]

    async def test_broken_envelope_on_a_valid_name_raises_in_listing(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "mine", "x")
        docs_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "documents"
        (docs_dir / "broken.md").write_text("no envelope here")
        with pytest.raises(MemoryFormatUnsupportedError):
            await store.list_documents(_SCOPE)


class TestPlantedDocuments:
    async def test_write_never_reissues_a_declared_version(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        # A document dropped in by hand (no sidecar) declaring version 5:
        # the next write must go to 6, not 1.
        docs_dir = tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "documents"
        docs_dir.mkdir(parents=True)
        (docs_dir / "planted.md").write_text(
            "---\nneosian_format: 1\nversion: 5\n"
            "created_at: '2026-08-19T09:00:00Z'\nupdated_at: '2026-08-19T09:00:00Z'\n"
            "---\nplanted",
            encoding="utf-8",
            newline="",
        )
        document = await store.write(_SCOPE, "planted", "updated")
        assert document.version == 6


class TestRedactionTrail:
    async def test_redact_appends_a_trail_line_with_actor(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await store.write(_SCOPE, "a", "secret")
        await store.write(_SCOPE, "b", "secret")
        count = await store.redact(_SCOPE, actor="conv-erase")
        assert count == 2
        trail = (
            tmp_path / "memory" / "user%3A123" / "proj%3Aerp" / "redactions.jsonl"
        ).read_text(encoding="utf-8")
        record = json.loads(trail.splitlines()[0])
        assert record["actor"] == "conv-erase"
        assert record["path"] is None
        assert record["count"] == 2
        assert record["ts"].endswith("Z")

    async def test_noop_redact_writes_no_trail(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        assert await store.redact("user:ghost") == 0
        assert list((tmp_path / "memory").iterdir()) == []


class TestClockDiscipline:
    async def test_injected_clock_stamps_documents(self, store: FileStore) -> None:
        document = await store.write(_SCOPE, "doc", "x")
        assert document.created_at == datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)

    async def test_created_at_survives_updates_strictly(self, store: FileStore) -> None:
        first = await store.write(_SCOPE, "doc", "v1")
        second = await store.write(_SCOPE, "doc", "v2")
        assert second.created_at == first.created_at
        assert second.updated_at > first.updated_at  # ManualClock is strict

    async def test_naive_clock_is_refused(self, tmp_path: Path) -> None:
        class NaiveClock:
            def now(self) -> datetime:
                return datetime(2026, 8, 19)

        store = FileStore(tmp_path / "m", clock=NaiveClock())
        with pytest.raises(ValueError, match="naive"):
            await store.write(_SCOPE, "doc", "x")


class TestLockSerialization:
    async def test_concurrent_writes_issue_gapless_versions(
        self, store: FileStore
    ) -> None:
        await asyncio.gather(
            *(store.write(_SCOPE, "doc", f"content {i}") for i in range(10))
        )
        rows = await store.versions(_SCOPE, "doc")
        assert [row.version for row in rows] == list(range(10, 0, -1))


class TestManualClockFixture:
    def test_each_read_advances(self) -> None:
        clock = ManualClock()
        assert clock.now() < clock.now()
