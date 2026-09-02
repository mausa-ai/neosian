"""Private file I/O (ledger #126): files `0600`, directories `0700`, atomic
replace, opt-in fsync — and an explicit mode for files the library does
not own."""

import os
import sys
from pathlib import Path

import pytest

from neosian._foundation.shared.fileio import (
    PRIVATE_DIR,
    PRIVATE_FILE,
    append_line,
    atomic_write,
    private_mkdir,
)


def _posix_only() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class TestPrivateMkdir:
    def test_every_created_level_is_private(self, tmp_path: Path) -> None:
        _posix_only()
        target = tmp_path / "a" / "b" / "c"
        private_mkdir(target)
        for level in (target, target.parent, target.parent.parent):
            assert _mode(level) == PRIVATE_DIR

    def test_existing_levels_are_left_alone(self, tmp_path: Path) -> None:
        _posix_only()
        existing = tmp_path / "open"
        existing.mkdir()
        existing.chmod(0o755)
        private_mkdir(existing / "inner")
        assert _mode(existing) == 0o755
        assert _mode(existing / "inner") == PRIVATE_DIR

    def test_idempotent(self, tmp_path: Path) -> None:
        private_mkdir(tmp_path / "x")
        private_mkdir(tmp_path / "x")
        assert (tmp_path / "x").is_dir()


class TestAtomicWrite:
    def test_private_by_default(self, tmp_path: Path) -> None:
        _posix_only()
        file = tmp_path / "doc.md"
        atomic_write(file, "body")
        assert file.read_text() == "body"
        assert _mode(file) == PRIVATE_FILE

    def test_an_explicit_mode_survives_the_umask(self, tmp_path: Path) -> None:
        _posix_only()
        file = tmp_path / "config.json"
        previous = os.umask(0o077)
        try:
            atomic_write(file, "{}", mode=0o644)
        finally:
            os.umask(previous)
        assert _mode(file) == 0o644

    def test_replaces_whole_and_leaves_no_temp(self, tmp_path: Path) -> None:
        file = tmp_path / "doc.md"
        atomic_write(file, "first")
        atomic_write(file, "second", fsync=True)
        assert file.read_text() == "second"
        assert list(tmp_path.glob(".neosian-tmp-*")) == []


class TestAppendLine:
    def test_a_new_file_is_private_and_appends(self, tmp_path: Path) -> None:
        _posix_only()
        file = tmp_path / "rows.jsonl"
        append_line(file, "one\n")
        append_line(file, "two\n", fsync=True)
        assert file.read_text() == "one\ntwo\n"
        assert _mode(file) == PRIVATE_FILE

    def test_an_existing_file_keeps_its_mode(self, tmp_path: Path) -> None:
        _posix_only()
        file = tmp_path / "rows.jsonl"
        file.write_text("one\n")
        file.chmod(0o644)
        append_line(file, "two\n")
        assert file.read_text() == "one\ntwo\n"
        assert _mode(file) == 0o644
