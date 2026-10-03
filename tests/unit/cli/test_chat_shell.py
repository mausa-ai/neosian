"""The resident agent's shell tool (NY2 slice B; DESIGN §35.2): the verbs
by class, the consent gate, the literal shell spawned on this interpreter,
and a write waiting on the session's modal. Zero keys."""

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot

import neosian._cli.chat_shell as chat_shell
from neosian import AgentConfig, Model
from neosian._cli.chat import open_chat, opening, streams, turn_title
from neosian._cli.chat_agent import resident_config
from neosian._cli.chat_cmd import one_shot
from neosian._cli.chat_shell import (
    NAME,
    Consent,
    create_shell_tool,
    reads,
    refusal,
    run,
)
from neosian._cli.tui.app import SessionApp
from neosian._cli.tui.widgets import Confirm, Prompt, ToolCall
from neosian._foundation.agent.approval import ToolApprovalRequest
from neosian._foundation.llm.base import ToolCall as Call
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian._foundation.tools.base import ToolResult, get_tool_definition
from tests.unit.cli.piloting import plain

_WRITE = ["setup", "--write", "--yes"]


@pytest.fixture(autouse=True)
def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _request(args: object, name: str = NAME) -> ToolApprovalRequest:
    return ToolApprovalRequest(
        call_id=ToolCallId("c1"), name=ToolName(name), arguments={"args": args}
    )


def _resident(*turns: FakeTurn) -> AgentConfig:
    """The resident agent on a scripted fake, its gate and tools intact."""
    fake = FakeClient(FakeScript(turns=turns))
    config = resident_config(Model.FAKE)
    return AgentConfig(
        system_prompt=config.system_prompt,
        tools=config.tools,
        model=Model.FAKE,
        enable_todo=False,
        memory=config.memory,
        tool_gate=config.tool_gate,
        client_factory=lambda _: fake,
    )


def _calls(*args: str) -> FakeTurn:
    call = Call(id=ToolCallId("c1"), name=ToolName(NAME), arguments={"args": [*args]})
    return FakeTurn(tool_calls=(call,))


@pytest.mark.unit
class TestTheVerbsByClass:
    @pytest.mark.parametrize(
        "args",
        [
            ["status"],
            ["docs", "cli"],
            ["version"],
            ["search", "espresso"],
            ["audit", "--since", "2026-10-01"],
            ["continue", "s1"],
            ["memory", "view", "/"],
            ["memory", "versions", "/project/handoff"],
            ["messages", "list", "--status", "all"],
            ["messages", "view", "--message-id", "m1"],
            ["setup"],
            ["setup", "--client", "codex"],
            ["configure"],
            ["configure", "--list", "--json"],
            ["update", "--help"],
            ["import", "-h"],
        ],
    )
    def test_a_form_that_only_reads_runs(self, args: list[str]) -> None:
        assert refusal(args) is None and reads(args)

    @pytest.mark.parametrize(
        "args",
        [
            ["setup", "--write"],
            ["setup", "--wri", "--yes"],  # argparse takes the prefix
            ["update"],
            ["update", "--check"],  # it contacts PyPI (#214)
            ["export", "backup"],
            ["import", "backup"],
            ["memory", "delete", "/project/handoff"],
            ["memory", "create", "/x", "--content", "y"],
            ["memory", "redact", "/x"],
            ["memory", "maintain"],
            ["memory", "--scope", "user:me", "view", "/"],  # unknown shape: ask
            ["memory"],
            ["messages", "send", "--body", "hi"],
            ["messages", "ack", "--message-id", "m1"],
            ["configure", "--delete"],
            ["configure", "--provider", "openai"],
        ],
    )
    def test_every_other_form_asks(self, args: list[str]) -> None:
        assert refusal(args) is None and not reads(args)

    @pytest.mark.parametrize(
        ("args", "said"),
        [
            ([], "name a verb"),
            (["chat", "hi"], "does not run inside the chat"),
            (["playground", "agent.py"], "does not run inside the chat"),
            (["eval", "suite.yaml"], "does not run inside the chat"),
            (["mcp"], "does not run inside the chat"),
            (["record"], "does not run inside the chat"),
            (["serve"], "does not run inside the chat"),
            (["rm", "-rf"], "unknown verb 'rm'"),
            (["configure", "--provider", "openai", "--key", "-"], "API key"),
            (["configure", "--ke", "sk-123"], "API key"),
        ],
    )
    def test_what_never_runs_from_the_chat(self, args: list[str], said: str) -> None:
        assert said in str(refusal(args))

    def test_every_verb_of_the_shell_has_a_class(self) -> None:
        """A verb added to the shell is classified here or fails this."""
        from neosian._cli.main import app

        verbs = {
            command.name or getattr(command.callback, "__name__", "")
            for command in app.registered_commands
        }
        assert verbs == chat_shell._VERBS | chat_shell._OUTSIDE


@pytest.mark.unit
class TestConsent:
    async def test_reads_refusals_and_other_tools_pass(self) -> None:
        consent = Consent()  # no one to ask: none of these needs it
        for request in (
            _request(["status"]),
            _request(["chat"]),  # the tool refuses in its own words
            _request("setup --write"),  # not an argv: the tool's validation
            _request(_WRITE, name="memory"),
        ):
            assert (await consent(request)).approved

    async def test_a_write_with_no_one_to_ask_is_declined_by_name(self) -> None:
        decision = await Consent()(_request(_WRITE))
        assert not decision.approved
        assert "`neosian setup --write --yes`" in str(decision.reason)
        assert "tell the user the command" in str(decision.reason)

    @pytest.mark.parametrize("answer", [True, False])
    async def test_a_write_asks_and_takes_the_answer(self, answer: bool) -> None:
        asked: list[str] = []

        async def ask(command: str) -> bool:
            asked.append(command)
            return answer

        consent = Consent()
        consent.ask = ask
        decision = await consent(_request(_WRITE))
        assert asked == ["neosian setup --write --yes"]
        assert decision.approved is answer
        assert (decision.reason == "the user declined it") is not answer


@pytest.mark.unit
class TestTheTool:
    def test_it_is_wired_from_the_pack(self) -> None:
        definition = get_tool_definition(create_shell_tool())
        assert definition is not None and definition.name == "neosian"
        assert definition.description == get_prompt("tools.neosian")
        assert definition.parameters["properties"]["args"]["description"] == (
            get_prompt_params("tools.neosian_params")["args"]
        )

    async def test_a_refused_form_spawns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def spawned(*_: object, **__: object) -> None:
            raise AssertionError("a refused form reached the shell")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawned)
        result = await create_shell_tool()(args=["serve"])
        assert not result.success and "does not run inside the chat" in str(
            result.error
        )

    async def test_status_comes_back_as_the_report(self, tmp_path: Path) -> None:
        result = await run(["status"])
        assert result.success and isinstance(result.data, dict)
        assert result.data["home"] == str(tmp_path / "home")
        assert "clients" in result.data and "providers" in result.data

    async def test_text_stays_text(self) -> None:
        result = await run(["memory", "--help"])
        assert result.success and "usage: neosian memory" in str(result.data)

    async def test_a_failed_verb_says_its_exit_and_why(self) -> None:
        result = await run(["search"])
        assert not result.success and str(result.error).startswith("exit 2:")
        assert "usage" in str(result.error)

    async def test_a_verb_that_overruns_is_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(chat_shell, "_TIMEOUT_SECONDS", 0.001)
        result = await run(["status"])
        assert not result.success and "was stopped" in str(result.error)


@pytest.mark.unit
class TestTheResidentAgentRunsTheVerbs:
    async def test_one_shot_answers_from_a_report(self) -> None:
        config = _resident(_calls("status"), FakeTurn(content="all wired"))
        out = io.StringIO()
        await one_shot(
            config, "set up?", conversation_id="o1", json_output=True, out=out
        )
        envelope = json.loads(out.getvalue())
        assert envelope["text"] == "all wired"
        assert envelope["tool_calls"] == [
            {"name": "neosian", "arguments": {"args": ["status"]}}
        ]

    async def test_one_shot_declines_a_write_and_spawns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[Any] = []

        async def never(args: Any) -> ToolResult[Any]:
            ran.append(args)
            return ToolResult.ok("ran")

        monkeypatch.setattr(chat_shell, "run", never)
        config = _resident(_calls(*_WRITE), FakeTurn(content="run it yourself"))
        convo = open_chat(config, conversation_id="o2")
        response = await convo.send("wire my agents")
        assert ran == []
        (result,) = response.tool_results
        assert not result.success and "tell the user the command" in str(result.error)


def _session(config: AgentConfig) -> SessionApp:
    """`run_chat`'s wiring: the gate's question is the app's modal."""
    convo = open_chat(config, conversation_id="w1")
    app = SessionApp(
        convo,
        opening(config, "neosian", convo, resumed=False),
        model=Model.FAKE,
        title=turn_title(config),
        streamed=streams(config),
    )
    assert config.tool_gate is not None
    assert isinstance(config.tool_gate.approver, Consent)
    config.tool_gate.approver.ask = app.confirm
    return app


async def _asked(pilot: Pilot[None]) -> None:
    for _ in range(200):
        if isinstance(pilot.app.screen, Confirm):
            return
        await pilot.pause(0.01)
    raise AssertionError("the question never came")


@pytest.mark.unit
class TestAWriteWaitsForTheHuman:
    @pytest.fixture
    def ran(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        ran: list[list[str]] = []

        async def stub(args: list[str]) -> ToolResult[Any]:
            ran.append(list(args))
            return ToolResult.ok({"written": True})

        monkeypatch.setattr(chat_shell, "run", stub)
        return ran

    async def _turn(self, pilot: Pilot[None], *keys: str) -> None:
        pilot.app.query_one(Prompt).text = "wire my agents"
        await pilot.press("enter")
        await _asked(pilot)
        assert "neosian setup --write --yes" in plain(
            pilot.app.screen.query_one("Static").content  # type: ignore[attr-defined]
        )
        await pilot.press(*keys)
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

    async def test_yes_runs_it(self, ran: list[list[str]]) -> None:
        app = _session(_resident(_calls(*_WRITE), FakeTurn(content="wired")))
        async with app.run_test() as pilot:
            await self._turn(pilot, "y")
            line = plain(app.query_one(ToolCall).content)
        assert ran == [_WRITE]
        assert "→ neosian setup --write --yes" in line and "written" in line

    @pytest.mark.parametrize("key", ["n", "escape"])
    async def test_no_declines_it_in_band(self, ran: list[list[str]], key: str) -> None:
        app = _session(_resident(_calls(*_WRITE), FakeTurn(content="declined")))
        async with app.run_test() as pilot:
            await self._turn(pilot, key)
            line = plain(app.query_one(ToolCall).content)
        assert ran == [] and "the user declined it" in line

    async def test_an_enter_typed_ahead_never_approves(
        self, ran: list[list[str]]
    ) -> None:
        app = _session(_resident(_calls(*_WRITE), FakeTurn(content="declined")))
        async with app.run_test() as pilot:
            await self._turn(pilot, "enter", "enter", "n")
        assert ran == []

    async def test_a_read_never_asks(self, ran: list[list[str]]) -> None:
        app = _session(_resident(_calls("status"), FakeTurn(content="fine")))
        async with app.run_test() as pilot:
            app.query_one(Prompt).text = "set up?"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, Confirm)
        assert ran == [["status"]]
