"""A registered model rides the memory harness across every shipped
transport — keylessly (DESIGN §19.7).

NC2 slice B's candidate lanes run exactly this path against real
endpoints; here the scripted pack proves the harness itself carries a
`RegisteredModel` through `function`, `cli`, `http` and `mcp` — the scripted
factory injects FakeClient whatever the provider row.
"""

import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian import OpenAICompatible, register_model
from neosian.evaluation import MemoryEvalConfig, load_eval_config, run_evaluation

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOOR = OpenAICompatible(
    name="example",
    api_key_env="EXAMPLE_API_KEY",
    base_url="https://api.example.com/v1",
)


@pytest.mark.unit
async def test_a_registered_model_rides_every_transport(tmp_path: Path) -> None:
    model = register_model(
        "example-large",
        provider=_DOOR,
        context_window=131_072,
        max_output_tokens=16_384,
    )
    config = load_eval_config(_REPO_ROOT / "examples" / "eval_memory_baseline.yaml")
    assert isinstance(config, MemoryEvalConfig)
    config = dataclasses.replace(
        config,
        agent=str(_REPO_ROOT / config.agent),
        models=(model,),
        scenarios=config.scenarios[:1],
    )
    with patch.dict(os.environ, {}, clear=True):  # no key anywhere
        report = await run_evaluation(config, store_root=tmp_path / "stores")
    assert report.models == ("example-large",)
    assert report.variants == ("function", "cli", "http", "mcp")
    failures = [f for r in report.results for t in r.turns for f in t.failures]
    assert report.failed == 0, failures
    assert all(r.error is None for r in report.results)
