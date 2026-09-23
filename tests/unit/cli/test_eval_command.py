"""`neosian eval` — rides the public facade and exits as a CI gate."""

import json
import textwrap
from pathlib import Path

import pytest
import typer

import neosian._cli.providers as providers
from neosian._cli.eval_cmd import run_eval
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

    def test_json_prints_the_artifact_document(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(_suite(tmp_path, "hi")), json_output=True)
        assert excinfo.value.exit_code == 0
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert payload["summary"] == {"total": 1, "passed": 1, "failed": 0}
        assert payload["schema"] == 2

    def test_a_load_error_exits_one(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(tmp_path / "absent.yaml"))
        assert excinfo.value.exit_code == 1


def _progress(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the command asks of the live tree, instead of drawing it."""
    asked: list[str] = []

    class Progress:
        def __init__(self, _config: object) -> None:
            asked.append("built")

        def start(self) -> None:
            asked.append("start")

        def stop(self) -> None:
            asked.append("stop")

        def update(self, _event: object) -> None:
            asked.append("tick")

    monkeypatch.setattr("neosian.evaluation.EvalProgress", Progress)
    return asked


@pytest.mark.unit
class TestOutputAndTheTree:
    """EC-10: the artifact's directory is a flag, and the live tree draws
    only where it can."""

    def test_output_names_the_artifacts_directory(self, tmp_path: Path) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            evaluate(str(_suite(tmp_path, "hi")), output=str(tmp_path / "reports"))
        assert excinfo.value.exit_code == 0
        assert len(list((tmp_path / "reports").glob("*.json"))) == 1
        assert not (tmp_path / ".neosian").exists()

    def test_the_tree_is_live_on_a_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = _progress(monkeypatch)
        suite = str(_suite(tmp_path, "hi"))
        assert run_eval(suite, json_output=False, output=None, live=True) == 0
        assert asked[:2] == ["built", "start"] and asked[-1] == "stop"
        assert "tick" in asked

    @pytest.mark.parametrize(("json_output", "live"), [(False, False), (True, True)])
    def test_never_off_a_terminal_or_under_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        json_output: bool,
        live: bool,
    ) -> None:
        asked = _progress(monkeypatch)
        suite = str(_suite(tmp_path, "hi"))
        assert run_eval(suite, json_output=json_output, output=None, live=live) == 0
        assert asked == []

    def test_a_run_that_raises_exits_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        async def run_evaluation(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("neosian.evaluation.run_evaluation", run_evaluation)
        suite = str(_suite(tmp_path, "hi"))
        assert run_eval(suite, json_output=True, output=None, live=False) == 1
        assert json.loads(capsys.readouterr().out) == {
            "error": "evaluation failed: boom",
            "hint": None,
        }
