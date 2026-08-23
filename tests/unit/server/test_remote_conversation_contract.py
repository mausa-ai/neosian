"""RemoteStore through the shipped ConversationStoreContract — the §9.7
kit over the wire (DESIGN §18), backing FileStore in hand for planting."""

from collections.abc import AsyncIterator

import pytest

from neosian import RemoteStore
from neosian._foundation.conversation.testing import ConversationStoreContract

from .conftest import RemoteOverFile


class TestRemoteConversationContract(ConversationStoreContract):
    _harness: RemoteOverFile

    @pytest.fixture
    async def store(
        self, remote_over_file: RemoteOverFile
    ) -> AsyncIterator[RemoteStore]:
        self._harness = remote_over_file
        yield remote_over_file.remote

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
        file = self._harness.root / "conversations" / conversation_id / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")
