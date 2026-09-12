"""FileStore run through the shipped ConversationStoreContract — the same
suite a host store's test suite inherits (DESIGN §9.7)."""

from pathlib import Path

import pytest

from neosian._foundation.memory.file import FileStore
from neosian.conversation.testing import ConversationStoreContract
from tests.support.clock import ManualClock


class TestFileStoreConversationContract(ConversationStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path, manual_clock: ManualClock) -> FileStore:
        return FileStore(tmp_path / "contract", clock=manual_clock)

    async def plant_raw_turn(
        self,
        store: FileStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        _plant(store, conversation_id, "turns.jsonl", line)

    async def plant_raw_projection(
        self,
        store: FileStore,  # type: ignore[override]
        conversation_id: str,
        *,
        line: str,
    ) -> None:
        _plant(store, conversation_id, "projections.jsonl", line)


def _plant(store: FileStore, conversation_id: str, filename: str, line: str) -> None:
    root = store._root  # noqa: SLF001 — substrate hook, deliberately inside
    file = root / "conversations" / conversation_id / filename
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8", newline="") as handle:
        handle.write(line + "\n")
