"""Both Python kits through `RemoteStore` against the binary's state
process (R2 slice A, #369): the same conformance tests the container tier
runs over the image, over a real socket into the spawned binary on an
empty root, the bearer token enforced. Per-test isolation on the
long-running server is the kits' own override point: a fresh scope or
conversation id per test; planting reaches the root the test holds."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from neosian import RemoteStore
from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import Scope, parse_scope, scope_directory
from neosian.conversation.testing import ConversationStoreContract, ErasureContract
from neosian.memory.testing import MemoryStoreContract
from tests.differential.harness import TOKEN, free_port, wait_healthy


@dataclass(frozen=True)
class Served:
    url: str
    root: Path


@pytest.fixture(scope="module")
def served(
    rust_door: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Served]:
    base = tmp_path_factory.mktemp("kits")
    root = base / "home"
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}
    env.update(
        HOME=str(base), NEOSIAN_HOME=str(root), NEOSIAN_SERVE_TOKEN=TOKEN, NO_COLOR="1"
    )
    server = subprocess.Popen(
        [str(rust_door), "serve", "--root", str(root), "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=base,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(server, url)
        yield Served(url, root)
    finally:
        server.terminate()
        try:
            server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()


@pytest.fixture
async def remote(served: Served) -> AsyncIterator[RemoteStore]:
    store = await RemoteStore.connect(served.url, token=TOKEN)
    try:
        yield store
    finally:
        await store.aclose()


def _fresh_scope() -> Scope:
    return parse_scope(f"user:kit-{uuid.uuid4().hex[:12]}")


def _fresh_conversation() -> str:
    return f"kit-{uuid.uuid4().hex[:12]}"


def _stamped(store: RemoteStore, actor: str) -> str:
    return f"{store.client}/{actor}"


class TestBinaryMemoryContract(MemoryStoreContract):
    _served: Served

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return _stamped(store, actor)

    @pytest.fixture
    def scope(self) -> Scope:
        return _fresh_scope()

    @pytest.fixture
    async def store(
        self, served: Served, remote: RemoteStore
    ) -> AsyncIterator[RemoteStore]:
        self._served = served
        yield remote

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
        segments = path_segments(path)
        doc_file = self._served.root.joinpath(
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


class TestBinaryConversationContract(ConversationStoreContract):
    _served: Served

    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return _stamped(store, actor)

    @pytest.fixture
    def conversation_id(self) -> str:
        return _fresh_conversation()

    @pytest.fixture
    async def store(
        self, served: Served, remote: RemoteStore
    ) -> AsyncIterator[RemoteStore]:
        self._served = served
        yield remote

    async def plant_raw_turn(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        self._plant(conversation_id, "turns.jsonl", line)

    async def plant_raw_projection(
        self,
        store: RemoteStore,  # type: ignore[override]  # noqa: ARG002 - unused
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        self._plant(conversation_id, "projections.jsonl", line)

    def _plant(self, conversation_id: str, filename: str, line: str) -> None:
        file = self._served.root / "conversations" / conversation_id / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")


class TestBinaryErasureContract(ErasureContract):
    def stamped(self, store: RemoteStore, actor: str) -> str:  # type: ignore[override]
        return _stamped(store, actor)

    @pytest.fixture
    def conversation_id(self) -> str:
        return _fresh_conversation()

    @pytest.fixture
    async def store(self, remote: RemoteStore) -> AsyncIterator[RemoteStore]:
        yield remote


class TestBinaryHandshake:
    async def test_the_handshake_names_the_file_store(
        self, remote: RemoteStore
    ) -> None:
        assert type(remote).supports_optimistic_concurrency is True
        capabilities = await remote.capabilities()
        assert capabilities["backend"] == "FileStore"
        assert capabilities["pageable"] is True
        assert capabilities["erasable"] is True
