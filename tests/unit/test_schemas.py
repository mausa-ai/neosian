"""The `python -m neosian.schemas` export surface (DESIGN §5/§6)."""

import json
from pathlib import Path

import pytest

from neosian._foundation.agent.event_schemas import AGENT_EVENT_SCHEMA_KEY
from neosian._foundation.agent.events import AgentEventType
from neosian.schemas import main


@pytest.mark.unit
def test_errors_command_prints_full_registry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["errors"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["llm_provider_error"] == {
        "exception": "ProviderError",
        "retryable": False,
    }
    assert payload["llm_tool_call_generation_failed"]["retryable"] is True
    from neosian._foundation.shared.exceptions import ERROR_CODES

    assert set(payload) == set(ERROR_CODES)


@pytest.mark.unit
def test_events_command_prints_all_schemas(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["events"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {t.value for t in AgentEventType} | {AGENT_EVENT_SCHEMA_KEY}
    assert len(payload[AGENT_EVENT_SCHEMA_KEY]["oneOf"]) == 10


@pytest.mark.unit
def test_events_out_writes_one_file_per_schema(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    assert main(["events", "--out", str(out)]) == 0
    names = {p.stem for p in out.glob("*.json")}
    assert names == {t.value for t in AgentEventType} | {AGENT_EVENT_SCHEMA_KEY}
    root = json.loads((out / f"{AGENT_EVENT_SCHEMA_KEY}.json").read_text())
    assert root["title"] == "AgentEvent"


@pytest.mark.unit
def test_unknown_command_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bogus"]) == 2
    assert "usage" in capsys.readouterr().err
