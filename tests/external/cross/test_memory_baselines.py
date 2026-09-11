"""Per-provider memory baselines over the shipped pack (DESIGN §13.12).

The same scenarios that gate the keyless FakeProvider tier run here
scriptless against each real provider — one source of scenario truth.
Since NA the pack carries `transports: [function, cli]`, so every
provider baseline also measures the model driving the shell grammar
(§14.3); the Anthropic axis test adds `native_memory`. A red run IS the
baseline doing its job: it means the provider's model broke the write
discipline, the recall, or the dedup behavior the pack pins. Store
roots land under the test's tmp dir for inspection.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import AnyModel, Model
from neosian.evaluation import (
    MemoryEvalConfig,
    Transport,
    run_evaluation,
)
from tests.external.board import (
    Board,
    Split,
    assert_board,
    board_id,
    boards,
    scriptless,
)
from tests.external.lanes import LANES, Lane
from tests.external.pacing import Pacer, door_client

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACK = _REPO_ROOT / "examples" / "eval_memory_baseline.yaml"
_TRANSPORTS = scriptless(_PACK, Model.FAKE).transports  # the pack's axis

_PROVIDER_CASES = [
    # One measured model per serving stack, the flagship rule: OpenAI's row
    # moved gpt-5-mini -> gpt-5.1 (2026-08-22) -> gpt-5.6-sol (NW1, #210).
    pytest.param(Model.GPT_5_6_SOL, "openai_api_key", "OPENAI_API_KEY", id="openai"),
    pytest.param(
        Model.CLAUDE_SONNET_5,
        "anthropic_api_key",
        "ANTHROPIC_API_KEY",
        id="anthropic",
    ),
    pytest.param(
        Model.CEREBRAS_GPT_OSS_120B,
        "cerebras_api_key",
        "CEREBRAS_API_KEY",
        id="cerebras",
    ),
    # A second measured row on one adapter (#210): a row is provider+model,
    # so this board says nothing about Qwen3.8-Max on Model Studio.
    pytest.param(
        Model.CEREBRAS_QWEN_3_8_27B,
        "cerebras_api_key",
        "CEREBRAS_API_KEY",
        id="cerebras-qwen",
    ),
]
# A door's board, or one per transport for a paced lane (board.py, #211).
BOARDS = [
    pytest.param(lane, split, id=board_id(lane, split))
    for lane, split in boards(LANES, _TRANSPORTS)
]


def _scriptless(
    model: AnyModel, transports: tuple[Transport, ...] | None = None
) -> MemoryEvalConfig:
    return scriptless(_PACK, model, transports)


class TestMemoryBaselines:
    @pytest.mark.parametrize(("model", "fixture", "env_name"), _PROVIDER_CASES)
    async def test_provider_baseline(
        self,
        model: Model,
        fixture: str,
        env_name: str,
        request: pytest.FixtureRequest,
        tmp_path: Path,
    ) -> None:
        key = request.getfixturevalue(fixture)  # skips when the env var is unset
        config = _scriptless(model)
        with patch.dict(os.environ, {env_name: key}, clear=True):
            report = await run_evaluation(config, store_root=tmp_path / "stores")
        assert_board(report)

    @pytest.mark.parametrize(("lane", "split"), BOARDS)
    async def test_door_baseline(
        self, lane: Lane, split: Split, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        """A door's row: a shipped catalog row is re-measured every dispatch
        like the three adapters; a candidate's cells are what membership
        reads — green over dispatched runs ships it, red exits. Every model
        call rides one paced clock (pacing.py) so an account tier never
        masquerades as model behavior, and the board runs under the lane's
        in-loop budget so a cut cell is a recorded `timeout` (board.py)."""
        key = request.getfixturevalue(lane.key_fixture)  # skips when unset
        board = Board(_scriptless(lane.registered(), split))
        pacer = Pacer.of(lane)
        with patch.dict(os.environ, {lane.door.api_key_env: key}, clear=True):
            report = await board.run(
                lane.budget_seconds,
                store_root=tmp_path / "stores",
                client_factory=lambda _provider: door_client(lane, key, pacer),
            )
        assert_board(report)

    async def test_anthropic_transport_axis(
        self, anthropic_api_key: str, tmp_path: Path
    ) -> None:
        """Function vs native memory_20250818 — the one run where the
        transport axis is informative (ledger #43)."""
        config = _scriptless(
            Model.CLAUDE_SONNET_5,
            transports=(Transport.FUNCTION, Transport.NATIVE),
        )
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": anthropic_api_key}, clear=True
        ):
            report = await run_evaluation(config, store_root=tmp_path / "stores")
        assert report.variants == ("function", "native_memory")
        assert_board(report)
