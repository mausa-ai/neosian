"""FileStore run through the shipped MemoryStoreContract conformance kit —
the same suite a host store's test suite inherits (ECOSYSTEM §10)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import parse_scope, scope_directory
from neosian.memory.testing import MemoryStoreContract
from tests.support.clock import ManualClock


class TestFileStoreContract(MemoryStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path, manual_clock: ManualClock) -> FileStore:
        return FileStore(tmp_path / "contract", clock=manual_clock)

    async def plant_raw_document(
        self,
        store: FileStore,  # type: ignore[override]
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        root = store._root  # noqa: SLF001 — substrate hook, deliberately inside
        segments = path_segments(path)
        doc_file = root.joinpath(
            *scope_directory(parse_scope(scope)),
            "documents",
            *segments[:-1],
            segments[-1] + ".md",
        )
        doc_file.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "neosian_format": format_version,
            "version": 1,
            "created_at": "2026-08-19T09:00:00Z",
            "updated_at": "2026-08-19T09:00:00Z",
            **extra,
        }
        rendered = yaml.safe_dump(frontmatter, sort_keys=False)
        doc_file.write_text(
            f"---\n{rendered}---\n{content}", encoding="utf-8", newline=""
        )
