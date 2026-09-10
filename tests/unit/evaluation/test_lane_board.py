"""The door lanes' board (tests/external/board.py), keylessly: the
per-transport split of a paced lane, and the in-loop ceiling that records a
cut cell as a `timeout` red instead of idling the runner (ledger #211)."""

import asyncio
import dataclasses
import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import AnyModel, BaseLLMClient, Message, Model
from neosian._foundation.llm.base import CompletionResponse, StreamChunk
from neosian.evaluation import EvalReport, Transport, run_evaluation
from neosian.fake import FakeClient
from tests.external.board import Board, assert_board, board_id, boards, scriptless
from tests.external.lanes import GEMINI, KIMI, LANES, XAI

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACK = _REPO_ROOT / "examples" / "eval_memory_baseline.yaml"
_TRANSPORTS = (Transport.FUNCTION, Transport.CLI)


class _HangsAfterTheFirstCell(BaseLLMClient):
    """The canned fake until the board has a finished cell, then a call
    that never returns — the slow provider a budget exists for."""

    def __init__(self, board: Board) -> None:
        self._board = board
        self._inner = FakeClient()

    async def _gate(self) -> None:
        if self._board.finished:
            await asyncio.Event().wait()

    async def complete(  # type: ignore[override]
        self, messages: list[Message], model: AnyModel, **_kwargs: object
    ) -> CompletionResponse:
        await self._gate()
        return await self._inner.complete(messages, model)

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: AnyModel, **_kwargs: object
    ) -> AsyncIterator[StreamChunk]:
        await self._gate()
        async for chunk in self._inner.stream(messages, model):
            yield chunk

    async def close(self) -> None:
        await self._inner.close()


def _cells(report: EvalReport) -> list[tuple[str, str, str | None]]:
    return [(c.variant, c.case, c.error) for c in report.results]


def _two_cells() -> Board:
    config = scriptless(_PACK, Model.FAKE, (Transport.FUNCTION,))
    return Board(dataclasses.replace(config, scenarios=config.scenarios[:2]))


@pytest.mark.unit
def test_a_paced_lane_splits_per_transport_and_an_unpaced_one_does_not() -> None:
    split = boards((XAI, KIMI), _TRANSPORTS)
    assert split == [
        (XAI, None),
        (KIMI, (Transport.FUNCTION,)),
        (KIMI, (Transport.CLI,)),
    ]
    assert [board_id(lane, s) for lane, s in split] == [
        "xai",
        "kimi-function",
        "kimi-cli",
    ]
    assert GEMINI.split_transports and not XAI.split_transports
    assert all(lane.budget_seconds < 3600 for lane in LANES)  # inside the outer mark


@pytest.mark.unit
async def test_a_cut_board_keeps_the_finished_cell_and_records_the_rest(
    tmp_path: Path,
) -> None:
    board = _two_cells()
    with patch.dict(os.environ, {}, clear=True):
        report = await board.run(
            1.0,
            store_root=tmp_path,
            client_factory=lambda _model: _HangsAfterTheFirstCell(board),
        )
    first, second = report.results
    assert first.error is None and first.turns  # finished, whole
    assert second.error is not None and second.error.startswith("timeout")
    assert not second.passed and second.variant == "function"
    assert report.total == 2 and report.cases == tuple(r.case for r in report.results)
    with pytest.raises(AssertionError, match="timeout"):
        assert_board(report)


@pytest.mark.unit
async def test_an_uncut_board_is_the_whole_run(tmp_path: Path) -> None:
    board = _two_cells()
    with patch.dict(os.environ, {}, clear=True):
        report = await board.run(
            60.0, store_root=tmp_path / "board", client_factory=lambda _m: FakeClient()
        )
        whole = await run_evaluation(
            board.config,
            store_root=tmp_path / "whole",
            client_factory=lambda _m: FakeClient(),
        )
    assert _cells(report) == _cells(whole)
    assert all(r.error is None for r in report.results)
