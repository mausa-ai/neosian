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

import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import Model
from neosian.evaluation import (
    EvalReport,
    MemoryEvalConfig,
    Transport,
    load_eval_config,
    run_evaluation,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACK = _REPO_ROOT / "examples" / "eval_memory_baseline.yaml"

_PROVIDER_CASES = [
    # OpenAI's row moved gpt-5-mini -> gpt-5.1 (user ruling, 2026-08-22):
    # the flagship chat model, measured per serving stack like every row.
    pytest.param(Model.GPT_5_1, "openai_api_key", "OPENAI_API_KEY", id="openai"),
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
]


def _scriptless(
    model: Model, transports: tuple[Transport, ...] | None = None
) -> MemoryEvalConfig:
    """The shipped pack with scripts stripped and one real model on the
    axis — derived in code so the scenario content never forks
    (ledger #68)."""
    config = load_eval_config(_PACK)
    assert isinstance(config, MemoryEvalConfig)
    scenarios = tuple(
        dataclasses.replace(
            scenario,
            sessions=tuple(
                dataclasses.replace(session, script=None)
                for session in scenario.sessions
            ),
        )
        for scenario in config.scenarios
    )
    return dataclasses.replace(
        config,
        agent=str(_REPO_ROOT / config.agent),
        models=(model,),
        scenarios=scenarios,
        transports=transports if transports is not None else config.transports,
    )


def _assert_baseline(report: EvalReport) -> None:
    harness_errors = [r.error for r in report.results if r.error is not None]
    assert not harness_errors, harness_errors
    failures = [
        (r.variant, r.case, f)
        for r in report.results
        for t in r.turns
        for f in t.failures
    ]
    assert report.failed == 0, failures


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
        _assert_baseline(report)

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
        _assert_baseline(report)
