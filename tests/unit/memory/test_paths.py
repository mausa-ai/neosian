"""Tests for the document-path grammar (memory/paths.py, DESIGN §8)."""

import pytest

from neosian._foundation.memory.paths import (
    PATH_MAX_LENGTH,
    PATH_MAX_SEGMENTS,
    path_segments,
    validate_document_path,
)
from neosian._foundation.shared.exceptions import MemoryPathInvalidError

_EXACT_512 = "/".join(["s" * 128] * 3 + ["s" * 125])
_OVER_512 = "/".join(["s" * 128] * 3 + ["s" * 126])
assert len(_EXACT_512) == 512
assert len(_OVER_512) == 513


class TestValidPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "notes",
            "notes/api",
            "a/b/c",
            "MEMORY.md",  # dots and extensions are just characters
            "..foo",  # only exactly "." and ".." are reserved
            "a..b",
            "...",
            "-dash",
            "_under",
            "s" * 128,  # 128-char segment
            "/".join(["s"] * 16),  # 16 segments — the ceiling
            _EXACT_512,
        ],
    )
    def test_accepted(self, path: str) -> None:
        validate_document_path(path)  # must not raise


class TestInvalidPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/a",  # leading slash
            "a/",  # trailing slash
            "a//b",  # empty segment
            ".",
            "..",
            "a/../b",  # reserved segment inside
            "a/.",
            "a\\b",  # backslash: separator on Windows, not in the charset
            "a b",  # whitespace
            "café",  # literal charset, never \w
            "a\n",  # anchoring
            "s" * 129,  # 129-char segment
            "/".join(["s"] * 17),  # 17 segments
            _OVER_512,  # 513 chars, every segment individually legal
        ],
    )
    def test_rejected(self, path: str) -> None:
        with pytest.raises(MemoryPathInvalidError) as exc_info:
            validate_document_path(path)
        assert exc_info.value.code == "memory_path_invalid"
        assert exc_info.value.details == {
            "path": path,
            "reason": exc_info.value.reason,
        }

    def test_limit_constants(self) -> None:
        assert PATH_MAX_LENGTH == 512
        assert PATH_MAX_SEGMENTS == 16


class TestPathSegments:
    def test_splits_on_slash(self) -> None:
        assert path_segments("a/b/c") == ("a", "b", "c")
        assert path_segments("single") == ("single",)
