"""The top-level help is grouped by audience (DESIGN §30) and ends with
the agents' door."""

import re

import pytest
from typer.testing import CliRunner

from neosian._cli.main import app

_ESCAPES = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """The help as text: a CI runner counts as a colour terminal to Rich,
    which styles the panels and the epilog with escapes."""
    return _ESCAPES.sub("", output)


def test_bare_neosian_under_a_pipe_prints_the_help() -> None:
    result = CliRunner().invoke(app, [], env={"NO_COLOR": "1"})
    assert result.exit_code == 0
    assert "Usage:" in _plain(result.output) and "Operate" in _plain(result.output)


def test_bare_neosian_on_a_terminal_opens_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import neosian._cli.chat_cmd as chat_cmd
    import neosian._cli.main as main

    calls: list[object] = []

    def fake(prompt: object, **kwargs: object) -> int:
        calls.append((prompt, kwargs))
        return 0

    monkeypatch.setattr(main, "_on_a_terminal", lambda: True)
    monkeypatch.setattr(chat_cmd, "run_chat_command", fake)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert calls == [
        (None, {"model": None, "agent": None, "resume": None, "json_output": False})
    ]


def test_the_help_names_the_four_audiences_and_the_agent_door() -> None:
    result = CliRunner().invoke(app, ["--help"], env={"NO_COLOR": "1"})
    assert result.exit_code == 0
    output = _plain(result.output)
    for panel in ("Talk", "Operate", "Connect an agent", "Learn"):
        assert panel in output
    assert "agents: neosian docs cli --json" in output
    # Every verb sits in a panel, in the audience order.
    order = [output.index(p) for p in ("Talk", "Operate", "Connect", "Learn")]
    assert order == sorted(order)
    assert output.index("status") > output.index("Operate")
