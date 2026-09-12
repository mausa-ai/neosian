"""IN-14: RemoteStore pages the two reads that have a cursor.

The paging is invisible — a caller gets one tuple, identical to the one
FileStore returns for the same call — so what these pins check is that
the answer is exact *and* that the wire actually carried it in pieces.

The four listings with no cursor (`list_documents`, `versions`,
`history`, `redactions`) cannot be done this way: `since` is a lower
bound over a newest-first order, and the others have no start key at
all. They wait for the `Pageable` protocol beside the ABCs.
"""

from __future__ import annotations

import pytest

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.server import remote as remote_module

from .conftest import RemoteOverFile


@pytest.fixture(autouse=True)
def _tiny_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four rows a page, so a handful of turns spans several requests."""
    monkeypatch.setattr(remote_module, "_PAGE", 4)


def _exchange(marker: str) -> list[Message]:
    return [
        Message(role=Role.USER, content=marker),
        Message(role=Role.ASSISTANT, content=f"re: {marker}"),
    ]


class TestReadTurnsPaging:
    async def test_every_turn_arrives_over_several_requests(
        self, remote_over_file: RemoteOverFile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for n in range(11):
            await remote_over_file.remote.append_turn("conv", _exchange(f"t{n}"))
        calls = 0
        original = type(remote_over_file.remote)._call

        async def counting(self, route, payload):  # type: ignore[no-untyped-def]
            nonlocal calls
            if route == "conversation/read_turns":
                calls += 1
            return await original(self, route, payload)

        monkeypatch.setattr(type(remote_over_file.remote), "_call", counting)
        turns = await remote_over_file.remote.read_turns("conv")
        assert [turn.turn for turn in turns] == list(range(1, 12))
        # 11 turns at 4 a page: three full pages and a short one.
        assert calls == 3
        # And the same answer the backing store gives, verbatim.
        direct = await remote_over_file.backing.read_turns("conv")
        assert turns == direct

    async def test_a_cursor_and_a_limit_are_exact(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        for n in range(11):
            await remote_over_file.remote.append_turn("conv", _exchange(f"t{n}"))
        for after, limit in ((0, 1), (0, 4), (0, 7), (3, 5), (9, None), (11, 3)):
            paged = await remote_over_file.remote.read_turns(
                "conv", after=after, limit=limit
            )
            direct = await remote_over_file.backing.read_turns(
                "conv", after=after, limit=limit
            )
            assert paged == direct, (after, limit)

    async def test_an_unknown_conversation_is_empty(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        assert await remote_over_file.remote.read_turns("nobody") == ()

    async def test_negative_cursors_stay_programmer_errors(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        with pytest.raises(ValueError):
            await remote_over_file.remote.read_turns("conv", after=-1)
        with pytest.raises(ValueError):
            await remote_over_file.remote.read_turns("conv", limit=-1)


class TestReadProjectionsPaging:
    async def _plant(self, harness: RemoteOverFile, per_turn: int, turns: int) -> None:
        entries = [
            ConversationProjection(turn=turn, kind="log", text=f"{turn}.{n}")
            for turn in range(1, turns + 1)
            for n in range(per_turn)
        ]
        await harness.remote.append_projections("conv", entries)

    @pytest.mark.parametrize("per_turn", [1, 2, 3])
    async def test_pages_stop_on_whole_turn_boundaries(
        self, remote_over_file: RemoteOverFile, per_turn: int
    ) -> None:
        await self._plant(remote_over_file, per_turn, turns=7)
        paged = await remote_over_file.remote.read_projections("conv")
        direct = await remote_over_file.backing.read_projections("conv")
        assert paged == direct
        assert len(paged) == per_turn * 7

    async def test_one_turn_wider_than_a_page_still_arrives_whole(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        # _PAGE is 4; turn 1 alone carries 9 entries, so the first page
        # holds a single turn and cannot be split. The page widens.
        await self._plant(remote_over_file, per_turn=9, turns=1)
        await self._plant(remote_over_file, per_turn=1, turns=0)
        paged = await remote_over_file.remote.read_projections("conv")
        direct = await remote_over_file.backing.read_projections("conv")
        assert paged == direct
        assert len(paged) == 9

    async def test_a_cursor_and_a_limit_are_exact(
        self, remote_over_file: RemoteOverFile
    ) -> None:
        await self._plant(remote_over_file, per_turn=2, turns=6)
        for after, limit in ((0, 1), (0, 4), (0, 9), (2, 3), (5, None), (99, 2)):
            paged = await remote_over_file.remote.read_projections(
                "conv", after=after, limit=limit
            )
            direct = await remote_over_file.backing.read_projections(
                "conv", after=after, limit=limit
            )
            assert paged == direct, (after, limit)
