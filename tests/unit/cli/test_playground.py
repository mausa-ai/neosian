"""`neosian playground` (DESIGN §14.6): an agent file under chat's run
tier. The grammar's refusals, the file as written, the model from
`--model` or the menu, the piped turn and its envelope; on the shipped
fake."""

import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import neosian._cli.models as models
import neosian._cli.playground as playground
from neosian._cli.main import app
from neosian._cli.playground import run_playground
from neosian._foundation.shared.registry import lookup_model
from neosian._foundation.shared.types import AgentConfig, AnyModel, Model

AGENT = """
from neosian import AgentConfig, Model, Tool, ToolResult


@Tool(name="greet", description="Greet someone.")
async def greet(name: str) -> ToolResult[str]:
    return ToolResult.ok(f"Hello {name}")


configuration = AgentConfig(
    system_prompt="You are a test agent.",
    model=Model.FAKE,
    tools=[greet],
    enable_todo=False,
)
"""

DOOR_AGENT = """
from neosian import AgentConfig, Model, OpenAICompatible, register_model

register_model(
    "acme-1",
    provider=OpenAICompatible(name="acme", api_key_env="ACME_API_KEY"),
    context_window=128_000,
    max_output_tokens=16_000,
)
configuration = AgentConfig(system_prompt="x", model=Model.FAKE, enable_todo=False)
"""


class _Terminal(io.StringIO):
    """A stdin that is a terminal: the menu may ask, a session may open."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # a directory with a name: the project layout


def _agent(tmp_path: Path, body: str = AGENT) -> str:
    path = tmp_path / "my_agent.py"
    path.write_text(body)
    return str(path)


def _run(
    agent: str,
    *,
    stdin: io.StringIO | None = None,
    model: str | None = None,
    menu: bool = False,
    json_output: bool = False,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_playground(
        agent,
        model=model,
        menu=menu,
        resume=None,
        json_output=json_output,
        stdin=stdin if stdin is not None else io.StringIO("hi\n"),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def _captured(monkeypatch: pytest.MonkeyPatch) -> list[AgentConfig]:
    """The config the shared tier would run, instead of running it."""
    seen: list[AgentConfig] = []

    def run_conversation(config: AgentConfig, _name: str, **_: object) -> int:
        seen.append(config)
        return 0

    monkeypatch.setattr(playground, "run_conversation", run_conversation)
    return seen


@pytest.mark.unit
class TestTheGrammar:
    def test_menu_and_model_are_exclusive(self) -> None:
        code, out, err = _run("absent.py", model="fake", menu=True, json_output=True)
        assert code == 2 and json.loads(out)["error"] == "usage"
        assert "exclusive" in err

    def test_the_menu_needs_a_terminal(self) -> None:
        code, out, err = _run("absent.py", menu=True)
        assert code == 2 and out == ""
        assert "--menu needs a terminal" in err and "--model" in err

    def test_a_file_that_cannot_load_exits_1(self, tmp_path: Path) -> None:
        code, out, err = _run(str(tmp_path / "absent.py"), json_output=True)
        assert code == 1
        assert "absent.py" in json.loads(out)["error"] and "absent.py" in err

    def test_an_unknown_model_is_grammar(self, tmp_path: Path) -> None:
        code, _, err = _run(_agent(tmp_path), model="nope")
        assert code == 2 and "unknown model 'nope'" in err


@pytest.mark.unit
class TestTheFile:
    def test_runs_as_written_without_chats_docs_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _captured(monkeypatch)
        assert _run(_agent(tmp_path))[0] == 0
        (config,) = seen
        assert [tool.__name__ for tool in config.tools] == ["greet"]
        assert config.model is Model.FAKE

    def test_model_overrides_the_files_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _captured(monkeypatch)
        assert _run(_agent(tmp_path), model="fake-small")[0] == 0
        (config,) = seen
        assert config.model is Model.FAKE_SMALL
        assert config.system_prompt == "You are a test agent."

    def test_model_may_name_a_door_the_file_registers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _captured(monkeypatch)
        assert _run(_agent(tmp_path, DOOR_AGENT), model="acme-1")[0] == 0
        assert seen[0].model is lookup_model("acme-1")


@pytest.mark.unit
class TestTheMenu:
    def _pick(self, monkeypatch: pytest.MonkeyPatch, chosen: AnyModel | None) -> None:
        def select(_console: object, *, require_reasoning: bool) -> AnyModel | None:
            assert require_reasoning is False  # the file sets no effort
            return chosen

        monkeypatch.setattr(models, "select_provider_and_model", select)

    def test_the_pick_is_the_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pick(monkeypatch, Model.FAKE_REASONING)
        seen = _captured(monkeypatch)
        assert _run(_agent(tmp_path), stdin=_Terminal(), menu=True)[0] == 0
        assert seen[0].model is Model.FAKE_REASONING

    def test_a_cancelled_pick_runs_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pick(monkeypatch, None)
        seen = _captured(monkeypatch)
        code, out, err = _run(_agent(tmp_path), stdin=_Terminal(), menu=True)
        assert (code, out, seen) == (0, "", []) and "cancelled" in err


@pytest.mark.unit
class TestThePipedTurn:
    def test_the_answer_on_stdout(self, tmp_path: Path) -> None:
        code, out, err = _run(_agent(tmp_path))
        assert (code, out, err) == (0, "fake response\n", "")

    def test_the_json_envelope(self, tmp_path: Path) -> None:
        code, out, _ = _run(_agent(tmp_path), json_output=True)
        assert code == 0 and out.count("\n") == 1
        payload = json.loads(out)
        assert payload["model"] == "fake" and payload["text"] == "fake response"
        assert payload["conversation_id"].endswith("-my_agent")

    def test_json_on_a_terminal_with_no_turn_is_grammar(self, tmp_path: Path) -> None:
        code, out, err = _run(_agent(tmp_path), stdin=_Terminal(), json_output=True)
        assert code == 2
        assert json.loads(out)["error"] == "usage" and "--json" in err


@pytest.mark.unit
class TestTheVerb:
    def test_the_flags_reach_the_run_tier(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            ["playground", _agent(tmp_path), "--model", "fake", "--json"],
            input="hi\n",
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["text"] == "fake response"

    def test_the_arena_is_gone(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ["playground", _agent(tmp_path), "--arena"])
        assert result.exit_code == 2
