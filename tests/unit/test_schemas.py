"""The `python -m neosian.schemas` export surface (DESIGN §5)."""

import json

import pytest

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
def test_unknown_command_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["events"]) == 2
    assert "usage" in capsys.readouterr().err
