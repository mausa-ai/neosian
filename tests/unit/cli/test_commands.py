"""The session's commands (NY2 slice C; DESIGN §35.4): seven, each where
asking the agent is impossible or wrong, and a resumed conversation's
turns drawn again. Zero keys."""

import stat
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Markdown, Static

from neosian import AgentConfig, Model
from neosian._cli.chat import open_chat, open_session
from neosian._cli.config import get_all_credentials, get_config_path
from neosian._cli.tui.app import SessionApp
from neosian._cli.tui.asks import Pick, Secret
from neosian._cli.tui.commands import BY_NAME, COMMANDS, NAMES
from neosian._cli.tui.widgets import Menu, Prompt, ToolCall
from neosian._foundation.llm.base import ToolCall as Call
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import ToolCallId, ToolFunction, ToolName
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.cli.piloting import plain, send, session_app

_KEY = "sk-secret-0123456789"


@pytest.fixture(autouse=True)
def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _config(*turns: FakeTurn, tools: tuple[ToolFunction, ...] = ()) -> AgentConfig:
    script = FakeScript(turns=turns or (FakeTurn(content="ok"),), repeat_last=True)
    fake = FakeClient(script)
    return AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        tools=list(tools),
    )


def _text(app: SessionApp) -> str:
    parts = []
    for widget in app.query("#transcript > *"):
        if isinstance(widget, Markdown):
            parts.append(widget.source)
        elif isinstance(widget, Static):
            parts.append(plain(widget.content))
    return "\n".join(parts)


async def _on(pilot: Pilot[None], screen: type) -> None:
    for _ in range(200):
        if isinstance(pilot.app.screen, screen):
            await pilot.pause()
            return
        await pilot.pause(0.01)
    raise AssertionError(f"{screen.__name__} never opened")


async def _type(pilot: Pilot[None], line: str) -> None:
    """Send a line whose command opens a modal: the worker is still running."""
    pilot.app.query_one(Prompt).text = line
    await pilot.press("enter")


async def _settled(pilot: Pilot[None]) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.unit
class TestTheSet:
    def test_there_are_seven_and_help_is_one(self) -> None:
        assert NAMES == (
            "help",
            "configure",
            "model",
            "resume",
            "new",
            "compact",
            "exit",
        )
        assert tuple(BY_NAME) == tuple(command.name for command in COMMANDS)

    async def test_help_lists_every_command_and_the_keys(self) -> None:
        app = session_app(_config())
        async with app.run_test(size=(110, 40)) as pilot:
            await send(pilot, "/help")
            text = _text(app)
        for name in NAMES:
            assert f"/{name}" in text
        assert "ctrl+o" in text and "ask in words" in text

    async def test_an_unknown_command_says_where_to_look(self, tmp_path: Path) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "/status")
            assert "no command /status: /help lists them" in _text(app)
        assert await FileStore(tmp_path / "home").read_turns("t1") == ()

    async def test_a_command_waits_for_the_running_turn(self) -> None:
        import asyncio

        release = asyncio.Event()

        @Tool(name="wait", description="Waits.")
        async def wait() -> ToolResult[str]:
            await release.wait()
            return ToolResult.ok("released")

        call = Call(id=ToolCallId("c1"), name=ToolName("wait"), arguments={})
        config = _config(
            FakeTurn(tool_calls=(call,)), FakeTurn(content="done"), tools=(wait,)
        )
        app = session_app(config)
        async with app.run_test() as pilot:
            await _type(pilot, "go")
            for _ in range(200):
                if app.query(ToolCall):
                    break
                await pilot.pause(0.01)
            await _type(pilot, "/new")
            await pilot.pause()
            assert app.query_one(Prompt).text == "/new"
            assert app.session.convo.conversation_id == "t1"
            release.set()
            await _settled(pilot)


@pytest.mark.unit
class TestTheMenu:
    """A typed `/` opens the commands as a menu above the prompt."""

    def _rows(self, app: SessionApp) -> list[str]:
        menu = app.query_one("#menu", Menu)
        return plain(menu.content).splitlines() if menu.display else []

    async def test_it_follows_what_is_typed(self) -> None:
        app = session_app(_config())
        async with app.run_test(size=(110, 30)) as pilot:
            assert self._rows(app) == []
            await pilot.press("/")
            rows = self._rows(app)
            assert [row.split()[-0 if row[0] != "›" else 1] for row in rows] == [
                f"/{name}" for name in NAMES
            ]
            assert (
                rows[0].startswith("› /help") and "the commands and the keys" in rows[0]
            )
            await pilot.press("c", "o")
            assert [row.split()[0:2] for row in self._rows(app)] == [
                ["›", "/configure"],
                ["/compact", "fold"],
            ]
            await pilot.press("x")  # nothing begins with /cox
            assert self._rows(app) == []
            await pilot.press("backspace", "backspace", "backspace", "backspace")
            assert self._rows(app) == []

    async def test_tab_completes_the_choice_and_leaves_room_for_an_argument(
        self,
    ) -> None:
        app = session_app(_config())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("/", "m", "tab")
            prompt = app.query_one(Prompt)
            assert prompt.text == "/model " and self._rows(app) == []
            await pilot.press(*"fake-small", "enter")
            await _settled(pilot)
            assert app.session.model is Model.FAKE_SMALL

    async def test_the_arrows_choose_and_enter_runs_the_choice(self) -> None:
        app = session_app(_config())
        async with app.run_test(size=(110, 30)) as pilot:
            await send(pilot, "first")
            await pilot.press("/", "down", "down")
            assert self._rows(app)[2].startswith("› /model")
            assert app.query_one(Prompt).text == "/"  # not the history
            await pilot.press("up", "up", "up")  # wraps past the top
            assert self._rows(app)[-1].startswith("› /exit")
            await pilot.press("down", "enter")
            await _settled(pilot)
            assert "/configure" in _text(app) and "ask in words" in _text(app)
            assert self._rows(app) == [] and app.query_one(Prompt).text == ""

    async def test_esc_puts_it_away_and_keeps_the_text(self) -> None:
        app = session_app(_config())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("/", "n", "escape")
            assert self._rows(app) == [] and app.query_one(Prompt).text == "/n"
            await pilot.press("e")
            assert self._rows(app)[0].startswith("› /new")


@pytest.mark.unit
class TestNewAndResume:
    async def test_new_starts_another_conversation(self, tmp_path: Path) -> None:
        app = session_app(_config(), "20260101-000000-probe")
        store = FileStore(tmp_path / "home")
        async with app.run_test() as pilot:
            await send(pilot, "first")
            await send(pilot, "/new")
            fresh = app.session.convo.conversation_id
            assert fresh != "20260101-000000-probe" and fresh.endswith("-probe")
            assert "> first" not in _text(app) and fresh in _text(app)
            await send(pilot, "second")
        assert len(await store.read_turns("20260101-000000-probe")) == 1
        assert len(await store.read_turns(fresh)) == 1

    async def test_resume_by_id_draws_its_turns_again(self) -> None:
        @Tool(name="read", description="Reads.")
        async def read() -> ToolResult[str]:
            return ToolResult.ok("one\ntwo")

        call = Call(id=ToolCallId("c1"), name=ToolName("read"), arguments={})
        earlier = _config(
            FakeTurn(tool_calls=(call,)), FakeTurn(content="it says two"), tools=(read,)
        )
        await open_chat(earlier, conversation_id="20260101-000000-probe").send("look")
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "/resume 20260101-000000-probe")
            text = _text(app)
            assert app.session.convo.conversation_id == "20260101-000000-probe"
            assert app.query_one(Prompt).has_focus
            await pilot.press("tab", "enter")
            call_view = app.query_one(ToolCall)
            assert call_view.has_focus and call_view.expanded
            # A session replacement can finish after focus leaves the prompt.
            await app.reopen(conversation_id="another", resumed=False)
            await pilot.pause()
            assert app.query_one(Prompt).has_focus and not app.query(ToolCall)
        assert "Resumed" in text and "> look" in text and "it says two" in text
        assert "→ read()" in text and "← one  (+1 lines)" in text

    async def test_resume_offers_this_chats_own_sessions(self) -> None:
        config = _config()
        for conversation_id, said in (
            ("20260101-000000-probe", "the older one"),
            ("20260202-000000-probe", "the newer one"),
            ("0f8fad5b-d9cb-469f-a165-70867728950e", "another agent's"),
        ):
            await open_chat(config, conversation_id=conversation_id).send(said)
        app = session_app(config)
        async with app.run_test() as pilot:
            await _type(pilot, "/resume")
            await _on(pilot, Pick)
            offered = plain(app.screen.query_one("OptionList").get_option_at_index(0).prompt)  # type: ignore[attr-defined]
            assert "20260202-000000-probe" in offered and "the newer one" in offered
            assert app.screen.query_one("OptionList").option_count == 2  # type: ignore[attr-defined]
            await pilot.press("down", "enter")
            await _settled(pilot)
            assert app.session.convo.conversation_id == "20260101-000000-probe"
            assert "> the older one" in _text(app)

    async def test_resume_with_nothing_to_offer_says_so(self) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "/resume")
            assert "no earlier session of this chat" in _text(app)

    async def test_backing_out_of_the_picker_changes_nothing(self) -> None:
        config = _config()
        await open_chat(config, conversation_id="20260101-000000-probe").send("x")
        app = session_app(config)
        async with app.run_test() as pilot:
            await _type(pilot, "/resume")
            await _on(pilot, Pick)
            await pilot.press("escape")
            await _settled(pilot)
            assert app.session.convo.conversation_id == "t1"

    async def test_a_started_session_opens_on_its_last_turns(self) -> None:
        config = _config()
        convo = open_chat(config, conversation_id="20260101-000000-probe")
        for number in range(1, 13):
            await convo.send(f"question {number}")
        session = await open_session(
            config, "probe", conversation_id="20260101-000000-probe", resumed=True
        )
        app = SessionApp(session, config=config, agent="probe")
        async with app.run_test():
            text = _text(app)
        assert "… 2 earlier turns are not drawn" in text
        assert "> question 2\n" not in text and "> question 3" in text
        assert "> question 12" in text


@pytest.mark.unit
class TestModel:
    async def test_a_named_model_takes_over_the_same_conversation(
        self, tmp_path: Path
    ) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "first")
            await send(pilot, "/model fake-small")
            assert app.session.model is Model.FAKE_SMALL
            assert app.session.convo.conversation_id == "t1"
            text = _text(app)
            assert "> first" in text and "model: fake/fake → fake/fake-small" in text
            assert "fake/fake-small" in plain(app.query_one("#footer", Static).content)
            await send(pilot, "second")
        assert len(await FileStore(tmp_path / "home").read_turns("t1")) == 2

    async def test_an_unknown_model_is_an_error_and_nothing_moves(self) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "/model gpt-nope")
            assert "Error: unknown model 'gpt-nope'" in _text(app)
            assert app.session.model is Model.FAKE

    async def test_the_picker_offers_only_what_a_key_opens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        app = session_app(_config())
        async with app.run_test() as pilot:
            await _type(pilot, "/model")
            await _on(pilot, Pick)
            options = app.screen.query_one("OptionList")
            labels = [
                plain(options.get_option_at_index(i).prompt)  # type: ignore[attr-defined]
                for i in range(options.option_count)  # type: ignore[attr-defined]
            ]
            assert labels and all("claude" in label for label in labels)
            await pilot.press("enter")
            await _settled(pilot)
            assert app.session.model.value.startswith("claude")

    async def test_no_key_anywhere_points_at_configure(self) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "/model")
            assert "no provider has a key yet: /configure" in _text(app)


@pytest.mark.unit
class TestConfigure:
    async def _enter(self, pilot: Pilot[None], *keys: str) -> None:
        await _type(pilot, "/configure")
        await _on(pilot, Pick)
        await pilot.press("down", "enter")  # openai, anthropic: the second row
        await _on(pilot, Secret)
        await pilot.press(*keys)
        await _settled(pilot)

    async def test_a_key_is_stored_and_never_shown_or_sent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "before")
            await self._enter(pilot, *_KEY, "enter")
            text = _text(app)
            await send(pilot, "after")
            assert os.environ["ANTHROPIC_API_KEY"] == _KEY
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        assert get_all_credentials()["anthropic_api_key"] == _KEY
        assert stat.S_IMODE(get_config_path().stat().st_mode) == 0o600
        assert "saved anthropic (ANTHROPIC_API_KEY)" in text and _KEY not in text
        turns = await FileStore(tmp_path / "home").read_turns("t1")
        assert len(turns) == 2 and _KEY not in repr(turns)

    async def test_backing_out_saves_nothing(self) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await self._enter(pilot, "s", "k", "escape")
            assert "no key entered: nothing was saved" in _text(app)
        assert not get_config_path().exists()


@pytest.mark.unit
class TestCompact:
    async def test_a_short_conversation_has_nothing_to_fold(self) -> None:
        app = session_app(_config())
        async with app.run_test() as pilot:
            await send(pilot, "hi")
            await send(pilot, "/compact")
            assert "nothing to fold yet" in _text(app)
