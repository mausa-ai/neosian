"""`run_evaluation(client_factory=…)` — the seam the candidate lanes pace
through (tests/external/pacing.py): the override serves every scriptless
model call, and scripted cells keep their FakeClient."""

import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import OpenAICompatible, register_model
from neosian.evaluation import (
    MemoryEvalConfig,
    Transport,
    load_eval_config,
    run_evaluation,
)
from neosian.fake import FakeClient

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOOR = OpenAICompatible(
    name="example",
    api_key_env="EXAMPLE_API_KEY",
    base_url="https://api.example.com/v1",
)


def _config(*, scripted: bool) -> MemoryEvalConfig:
    model = register_model(
        "example-large",
        provider=_DOOR,
        context_window=131_072,
        max_output_tokens=16_384,
    )
    config = load_eval_config(_REPO_ROOT / "examples" / "eval_memory_baseline.yaml")
    assert isinstance(config, MemoryEvalConfig)
    scenario = config.scenarios[0]
    if not scripted:
        scenario = dataclasses.replace(
            scenario,
            sessions=tuple(
                dataclasses.replace(session, script=None)
                for session in scenario.sessions
            ),
        )
    return dataclasses.replace(
        config,
        agent=str(_REPO_ROOT / config.agent),
        models=(model,),
        scenarios=(scenario,),
        transports=(Transport.FUNCTION,),
    )


@pytest.mark.unit
async def test_the_override_serves_scriptless_cells(tmp_path: Path) -> None:
    fake = FakeClient()  # the canned turn, repeated — no key, no script
    with patch.dict(os.environ, {}, clear=True):
        report = await run_evaluation(
            _config(scripted=False),
            store_root=tmp_path,
            client_factory=lambda _provider: fake,
        )
    assert fake.calls, "the scriptless cell never reached the injected client"
    assert all(r.error is None for r in report.results)  # no key was asked for


@pytest.mark.unit
async def test_scripted_cells_keep_their_script(tmp_path: Path) -> None:
    fake = FakeClient()
    with patch.dict(os.environ, {}, clear=True):
        report = await run_evaluation(
            _config(scripted=True),
            store_root=tmp_path,
            client_factory=lambda _provider: fake,
        )
    assert not fake.calls
    assert report.failed == 0
