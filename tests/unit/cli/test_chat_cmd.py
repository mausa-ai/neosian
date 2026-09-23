"""`neosian chat` (DESIGN §30.2): the model order, the one-shot envelope,
persistence under the home, the tiers — on the shipped fake."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import neosian._cli.chat_cmd as chat_cmd
from neosian._cli.chat_cmd import (
    ChatError,
    ChatUsageError,
    build_config,
    one_shot,
    resolve_chat_model,
    run_chat_command,
)
from neosian._cli.config import set_value
from neosian._cli.main import app
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.catalog import OpenAICompatible
from neosian._foundation.shared.registry import register_model
from neosian._foundation.shared.types import Model

_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY")


@pytest.fixture(autouse=True)
def _keyless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


class TestTheModel:
    def test_the_flag_wins(self) -> None:
        assert resolve_chat_model("fake", {"ANTHROPIC_API_KEY": "k"}) is Model.FAKE
        assert resolve_chat_model("gpt-5.6-sol", {}) is Model.GPT_5_6_SOL

    def test_an_unknown_flag_is_grammar(self) -> None:
        with pytest.raises(ChatUsageError):
            resolve_chat_model("nope", {})

    def test_the_config_file_is_next(self) -> None:
        set_value("chat", "model", "claude-sonnet-5")
        assert (
            resolve_chat_model(None, {"OPENAI_API_KEY": "k"}) is Model.CLAUDE_SONNET_5
        )
        set_value("chat", "model", "nope")
        with pytest.raises(ChatUsageError):
            resolve_chat_model(None, {})

    def test_then_the_first_keyed_provider_in_order(self) -> None:
        env = {"CEREBRAS_API_KEY": "c", "OPENAI_API_KEY": "o"}
        assert resolve_chat_model(None, env) is Model.GPT_5_6_SOL
        assert resolve_chat_model(None, {**env, "ANTHROPIC_API_KEY": "a"}) is (
            Model.CLAUDE_SONNET_5
        )
        assert resolve_chat_model(None, {"CEREBRAS_API_KEY": "c"}) is (
            Model.CEREBRAS_GPT_OSS_120B
        )

    @pytest.mark.parametrize(
        ("env_name", "model"),
        [
            ("XAI_API_KEY", Model.GROK_4_6),
            ("GEMINI_API_KEY", Model.GEMINI_3_8_FLASH),  # the door's first row
            ("MOONSHOT_API_KEY", Model.KIMI_K3),
            ("DASHSCOPE_API_KEY", Model.QWEN_3_8_MAX),
        ],
    )
    def test_then_a_shipped_door_row_with_a_key(
        self, env_name: str, model: Model
    ) -> None:
        # `configure` stores it, the loader exports it, `status` lists it:
        # a machine holding only this key opens a chat instead of exiting 1.
        assert resolve_chat_model(None, {env_name: "k"}) is model

    def test_a_shipped_door_comes_after_the_three_and_before_a_registered_one(
        self,
    ) -> None:
        door = OpenAICompatible(name="acme", api_key_env="ACME_API_KEY")
        register_model("acme-1", provider=door, context_window=9, max_output_tokens=9)
        env = {"XAI_API_KEY": "x", "ACME_API_KEY": "a"}
        assert resolve_chat_model(None, env) is Model.GROK_4_6
        assert resolve_chat_model(None, {**env, "CEREBRAS_API_KEY": "c"}) is (
            Model.CEREBRAS_GPT_OSS_120B
        )

    def test_then_a_registered_door_with_a_key(self) -> None:
        door = OpenAICompatible(name="acme", api_key_env="ACME_API_KEY")
        acme = register_model(
            "acme-1", provider=door, context_window=9, max_output_tokens=9
        )
        assert resolve_chat_model(None, {"ACME_API_KEY": "k"}) is acme

    def test_no_key_names_configure(self) -> None:
        with pytest.raises(ChatError) as excinfo:
            resolve_chat_model(None, {})
        assert "neosian configure" in excinfo.value.message


class TestTheConfig:
    def test_the_resident_agent_by_default(self) -> None:
        config, name = build_config("fake", None, {})
        assert name == "neosian" and config.model is Model.FAKE

    def test_an_agent_file_with_chats_tools_and_the_model_override(
        self, tmp_path: Path
    ) -> None:
        agent = tmp_path / "agent.py"
        agent.write_text(
            "from neosian import AgentConfig, Model\n"
            "configuration = AgentConfig(system_prompt='x', model=Model.FAKE_SMALL)\n"
        )
        config, name = build_config(None, str(agent), {})
        assert name == "agent" and config.model is Model.FAKE_SMALL
        assert [t.__name__ for t in config.tools] == ["docs"]
        assert build_config("fake", str(agent), {})[0].model is Model.FAKE


def _fake_config() -> "object":
    from dataclasses import replace

    from neosian._cli.chat_agent import resident_config

    fake = FakeClient(FakeScript(turns=(FakeTurn(content="noted"),)))
    return replace(resident_config(Model.FAKE), client_factory=lambda _: fake)


class TestOneShot:
    async def test_the_answer_and_the_persisted_turn(self, tmp_path: Path) -> None:
        out = io.StringIO()
        await one_shot(
            _fake_config(),  # type: ignore[arg-type]
            "remember: tabs",
            conversation_id="c1",
            json_output=False,
            out=out,
        )
        assert out.getvalue() == "noted\n"
        assert (tmp_path / "home" / "conversations" / "c1" / "turns.jsonl").exists()
        turns = await FileStore(tmp_path / "home").read_turns("c1")
        assert len(turns) == 1

    async def test_the_json_envelope(self) -> None:
        out = io.StringIO()
        await one_shot(
            _fake_config(),  # type: ignore[arg-type]
            "hi",
            conversation_id="c2",
            json_output=True,
            out=out,
        )
        payload = json.loads(out.getvalue())
        assert out.getvalue().count("\n") == 1
        assert payload["conversation_id"] == "c2" and payload["model"] == "fake"
        assert payload["text"] == "noted" and payload["tool_calls"] == []
        assert set(payload["usage"]) == {
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        }
        assert "cost_micro_usd" in payload


class TestRunTier:
    def _run(
        self, prompt: str | None, stdin: str = "", **flags: object
    ) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = run_chat_command(
            prompt,
            model=flags.get("model"),  # type: ignore[arg-type]
            agent=None,
            resume=None,
            json_output=bool(flags.get("json_output", False)),
            stdin=io.StringIO(stdin),
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    def test_a_prompt_argument_is_one_turn(self) -> None:
        code, out, err = self._run("hi", model="fake")
        assert code == 0 and err == ""
        assert out == "fake response\n"  # the router's canned fake

    def test_piped_stdin_is_one_turn(self) -> None:
        code, out, _ = self._run(None, stdin="hi\n", model="fake", json_output=True)
        assert code == 0
        assert json.loads(out)["text"] == "fake response"

    def test_empty_stdin_is_grammar(self) -> None:
        code, out, err = self._run(None, stdin="  \n", model="fake")
        assert code == 2 and out == "" and "nothing to say" in err

    def test_no_key_exits_1_naming_configure(self) -> None:
        code, out, err = self._run("hi", json_output=True)
        assert code == 1
        assert "neosian configure" in json.loads(out)["error"]
        assert "neosian configure" in err

    def test_an_unknown_model_exits_2(self) -> None:
        code, out, err = self._run("hi", model="nope", json_output=True)
        assert code == 2 and json.loads(out)["error"] == "usage" and "nope" in err


class _Terminal(io.StringIO):
    """A stdin that is a terminal: no piped turn, so a session opens."""

    def isatty(self) -> bool:
        return True


def _run_on(
    stdin: io.StringIO, prompt: str | None = None, **flags: Any
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_chat_command(
        prompt,
        model=flags.get("model", "fake"),
        agent=flags.get("agent"),
        resume=flags.get("resume"),
        json_output=bool(flags.get("json_output", False)),
        stdin=stdin,
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


class TestTheSharedTier:
    """`run_conversation`, the tier chat and playground share (§14.6)."""

    def _session(
        self, monkeypatch: pytest.MonkeyPatch, raises: BaseException | None = None
    ) -> list[dict[str, Any]]:
        opened: list[dict[str, Any]] = []

        async def run_chat(
            _console: object, _config: object, name: str, **kw: Any
        ) -> None:
            opened.append({"name": name, **kw})
            if raises is not None:
                raise raises

        monkeypatch.setattr(chat_cmd, "run_chat", run_chat)
        return opened

    def test_a_terminal_opens_the_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened = self._session(monkeypatch)
        code, out, _ = _run_on(_Terminal())
        assert code == 0 and out == ""
        assert [(o["name"], o["resumed"]) for o in opened] == [("neosian", False)]

    @pytest.mark.parametrize(
        ("raises", "code"), [(KeyboardInterrupt(), 130), (SystemExit(1), 1)]
    )
    def test_a_session_that_stops_keeps_its_tier(
        self, monkeypatch: pytest.MonkeyPatch, raises: BaseException, code: int
    ) -> None:
        self._session(monkeypatch, raises)
        assert _run_on(_Terminal())[0] == code

    def test_json_never_opens_a_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened = self._session(monkeypatch)
        code, out, err = _run_on(_Terminal(), json_output=True)
        assert code == 2 and opened == []
        assert json.loads(out)["error"] == "usage" and "--json" in err

    def test_a_bad_resume_id_is_grammar(self) -> None:
        code, _, err = _run_on(io.StringIO("hi"), resume="bad id!")
        assert code == 2 and "bad id!" in err

    @pytest.mark.parametrize(
        ("raises", "code"), [(KeyboardInterrupt(), 130), (RuntimeError("down"), 1)]
    )
    def test_a_turn_that_stops_keeps_its_tier(
        self, monkeypatch: pytest.MonkeyPatch, raises: BaseException, code: int
    ) -> None:
        async def one_shot(*_args: object, **_kwargs: object) -> None:
            raise raises

        monkeypatch.setattr(chat_cmd, "one_shot", one_shot)
        assert _run_on(io.StringIO("hi"))[0] == code

    def test_a_failed_turn_names_why_in_both_streams(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def one_shot(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("provider down")

        monkeypatch.setattr(chat_cmd, "one_shot", one_shot)
        code, out, err = _run_on(io.StringIO("hi"), json_output=True)
        assert code == 1 and json.loads(out)["error"] == "provider down"
        assert err == "error: provider down\n"

    def test_an_agent_file_that_cannot_load_exits_1(self, tmp_path: Path) -> None:
        code, _, err = _run_on(io.StringIO("hi"), agent=str(tmp_path / "absent.py"))
        assert code == 1 and "absent.py" in err


def test_the_verb_forwards_its_flags() -> None:
    result = CliRunner().invoke(
        app, ["chat", "--model", "fake", "--json"], input="hi\n"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["text"] == "fake response"
