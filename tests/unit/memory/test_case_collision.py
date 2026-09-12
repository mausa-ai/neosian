"""MC-13: a case-folding filesystem never merges two scopes or two paths.

The grammar admits mixed-case ids and never normalizes (ECOSYSTEM §2), so
`user:A` and `user:a` are two scopes everywhere and one directory on
APFS/NTFS. Both substrates are legal, so every test here states which
answer it expects on which — none is skipped.

IN-13 rides along at the bottom: the three reads that walk a whole scope
run in a worker thread, so the daemon's loop is never the thing waiting.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from neosian._foundation.memory import file_layout as layout
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.exceptions import MemoryConflictError


def _folds_case(root: Path) -> bool:
    """Does this filesystem fold case? Probed, never assumed."""
    probe = root / "NeosianCaseProbe"
    probe.mkdir()
    try:
        return (root / "neosiancaseprobe").exists()
    finally:
        probe.rmdir()


class TestScopeCaseCollision:
    def test_an_unwritten_scope_never_collides(self, tmp_path: Path) -> None:
        assert layout.scope_case_collision(tmp_path, "user:A") is None

    def test_the_same_spelling_never_collides(self, tmp_path: Path) -> None:
        (tmp_path / "user%3AA").mkdir()
        assert layout.scope_case_collision(tmp_path, "user:A") is None

    def test_a_folded_spelling_is_reported_only_where_it_folds(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "user%3AA").mkdir()
        collided = layout.scope_case_collision(tmp_path, "user:a")
        # The filesystem found a directory we never spelled that way —
        # or, where case is significant, two directories and nothing to
        # refuse.
        assert collided == ("user:A" if _folds_case(tmp_path) else None)

    def test_a_nested_segment_is_reported_by_its_own_name(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "user%3Aa" / "proj%3AERP").mkdir(parents=True)
        collided = layout.scope_case_collision(tmp_path, "user:a/proj:erp")
        assert collided == ("proj:ERP" if _folds_case(tmp_path) else None)


class TestFileStoreRefusesAFold:
    async def test_a_folded_scope_is_refused_where_it_folds(
        self, tmp_path: Path
    ) -> None:
        store = FileStore(tmp_path)
        await store.write("user:A", "notes/api", "first")
        if not _folds_case(tmp_path):
            # Two directories: the second scope is simply a second scope.
            document = await store.write("user:a", "notes/api", "second")
            assert document.version == 1
            return
        with pytest.raises(MemoryConflictError) as caught:
            await store.write("user:a", "notes/api", "second")
        assert caught.value.reason == "case_collision"
        assert caught.value.scope == "user:a"
        assert caught.value.path is None
        # The first scope is untouched: one document, one version counter.
        assert (await store.read("user:A", "notes/api")) is not None

    async def test_a_folded_path_is_refused_from_the_sidecar(
        self, tmp_path: Path
    ) -> None:
        """Substrate-independent: the row carries the spelling it was
        written under, so planting one proves the check without needing a
        filesystem that folds."""
        store = FileStore(tmp_path)
        root = tmp_path.resolve()
        await store.write("user:a", "notes/API", "first")
        planted = layout.journal_file(root, "user:a", "notes/api")
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(layout.journal_file(root, "user:a", "notes/API").read_text())
        with pytest.raises(MemoryConflictError) as caught:
            await store.write("user:a", "notes/api", "second")
        assert caught.value.reason == "case_collision"
        assert caught.value.path == "notes/api"
        # The planted row named the other spelling — that is the signal.
        row = json.loads(planted.read_text().splitlines()[0])
        assert row["path"] == "notes/API"

    async def test_a_scope_is_verified_once_per_instance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = FileStore(tmp_path)
        await store.write("user:a", "notes/api", "first")
        calls = 0

        def counting(root: Path, scope: str) -> str | None:
            del root, scope  # the count is the assertion
            nonlocal calls
            calls += 1
            return None

        monkeypatch.setattr(layout, "scope_case_collision", counting)
        await store.write("user:a", "notes/api", "second")
        await store.read("user:a", "notes/api")
        assert calls == 0
        # An unseen scope is checked once, then remembered.
        await store.write("user:b", "notes/api", "first")
        await store.read("user:b", "notes/api")
        assert calls == 1


class TestScopeWalksRunOffTheLoop:
    """IN-13: the three that walk a whole scope never stall the daemon."""

    @pytest.mark.parametrize(
        ("helper", "call"),
        [
            ("_list_documents", lambda s: s.list_documents("user:a")),
            ("_history", lambda s: s.history("user:a")),
            ("_redact", lambda s: s.redact("user:a")),
        ],
    )
    async def test_the_body_runs_in_a_worker_thread(
        self,
        tmp_path: Path,
        helper: str,
        call: Callable[[FileStore], Awaitable[object]],
    ) -> None:
        store = FileStore(tmp_path)
        await store.write("user:a", "notes/api", "x")
        original: Callable[..., Any] = getattr(store, helper)
        ran_on: list[int] = []

        def spy(*args: Any, **kwargs: Any) -> Any:
            ran_on.append(threading.get_ident())
            return original(*args, **kwargs)

        setattr(store, helper, spy)
        await call(store)
        assert len(ran_on) == 1
        assert ran_on[0] != threading.get_ident()
