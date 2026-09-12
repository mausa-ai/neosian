"""RemoteStore through the shipped MemoryStoreContract — the §8 kit over
the wire (DESIGN §18): the app runs in-process on `httpx.ASGITransport`,
the backing FileStore stays in hand for substrate planting, and every
call crosses the store-shaped HTTP API with the bearer token enforced."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
import yaml

from neosian import RemoteStore
from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import parse_scope, scope_directory
from neosian.memory.testing import MemoryStoreContract

from .conftest import RemoteOverFile


class TestRemoteMemoryContract(MemoryStoreContract):
    _harness: RemoteOverFile

    @pytest.fixture
    async def store(
        self, remote_over_file: RemoteOverFile
    ) -> AsyncIterator[RemoteStore]:
        self._harness = remote_over_file
        yield remote_over_file.remote

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return f"{store.client}/{actor}"

    async def plant_raw_document(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        scope: str,
        path: str,
        *,
        content: str,
        format_version: int,
        extra: Mapping[str, Any],
    ) -> None:
        # The backing FileStore's substrate, written directly — the
        # test_file_contract idiom, from the server's side of the wire.
        segments = path_segments(path)
        doc_file = self._harness.root.joinpath(
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
