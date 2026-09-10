"""`neosian eval` — rides the public facade and exits as a CI gate."""

import textwrap
from pathlib import Path

import pytest
import typer

import neosian._cli.providers as providers
from neosian._cli.main import evaluate

AGENT_FILE = """
from neosian import AgentConfig

configuration = AgentConfig(
    system_prompt="You are a test agent.",
    enable_todo=False,
)
"""

SUITE = """
name: gate
agent: {agent_path}
models: [fake]
cases:
  - name: c
    input: "say hi"
    expect:
      response: {{contains: "{expected}"}}
    script:
      - content: "hi there"
"""


def _suite(tmp_path: Path, expected: str) -> Path:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(textwrap.dedent(AGENT_FILE))
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(SUITE.format(agent_path=agent_path, expected=expected))
    return suite_path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(providers, "load_keys_into_env", lambda: None)
    monkeypatch.chdir(tmp_path)  # the artifact lands in tmp's .neosian/evals


@pytest.mark.unit
class TestExitCodes:
    def test_all_passing_exits_zero_and_writes_the_artifact(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(_suite(tmp_path, "hi")))
        assert excinfo.value.exit_code == 0
        artifacts = list((tmp_path / ".neosian" / "evals").glob("*.json"))
        assert len(artifacts) == 1

    def test_a_failing_case_exits_one(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(_suite(tmp_path, "goodbye")))
        assert excinfo.value.exit_code == 1

    def test_a_load_error_exits_one(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(tmp_path / "absent.yaml"))
        assert excinfo.value.exit_code == 1
