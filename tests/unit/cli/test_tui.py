"""The session view (NY2 slice A; DESIGN §35): the Textual app over one
Conversation, driven headless by Textual's pilot. Zero keys."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.pilot import Pilot
from textual.widgets import Markdown, Static

from neosian import AgentConfig, Model
from neosian._cli.chat import Opening
from neosian._cli.tui.app import SessionApp
from neosian._cli.tui.turn import TurnView
from neosian._cli.tui.widgets import Prompt, ToolCall, Working
from neosian._foundation.agent.events import (
    BlockedEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolProgressEvent,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, Role, ToolCall as Call
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.guardrail_types import GuardrailResult, PolicyResult
from neosian._foundation.shared.types import ToolCallId, ToolFunction, ToolName
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.cli.piloting import plain, send, session_app

_SYSTEM = "You are a test agent."


def _config(
    *turns: FakeTurn,
    memory: MemoryConfig | None = None,
    tools: tuple[ToolFunction, ...] = (),
) -> AgentConfig:
    fake = FakeClient(FakeScript(turns=turns, chunk_chars=4))
    return AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        memory=memory,
        tools=list(tools),
    )


def _app(
    config: AgentConfig, conversation_id: str = "t1", *, streamed: bool | None = None
) -> SessionApp:
    return session_app(config, conversation_id, streamed=streamed)


def _text(app: App[None]) -> str:
    """The transcript as a reader meets it, top to bottom."""
    parts = []
    for widget in app.query("#transcript > *"):
        if isinstance(widget, Markdown):
            parts.append(widget.source)
        elif isinstance(widget, Static):
            parts.append(plain(widget.content))
    return "\n".join(parts)


async def _until(pilot: Pilot[None], met: Callable[[], bool]) -> None:
    for _ in range(200):
        if met():
            return
        await pilot.pause(0.01)
    raise AssertionError("the app never got there")


def _call(call_id: str, name: str, **arguments: object) -> Call:
    return Call(id=ToolCallId(call_id), name=ToolName(name), arguments=arguments)


class _Host(App[None]):
    """A bare transcript: a turn's view without a session around it."""

    def compose(self) -> ComposeResult:
        yield Vertical(id="transcript")


@asynccontextmanager
async def _view() -> AsyncIterator[tuple[TurnView, _Host]]:
    host = _Host()
    async with host.run_test():
        transcript = host.query_one("#transcript", Vertical)
        yield TurnView(transcript, Model.FAKE, Text("fake/fake")), host


@pytest.fixture(autouse=True)
def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.unit
class TestAStreamedTurn:
    async def test_a_tool_call_and_its_memory_write_land_before_the_reply(
        self, tmp_path: Path
    ) -> None:
        memory = MemoryConfig(
            store=FileStore(tmp_path / "mem"),
            mounts=(Mount(scope="user:demo", mount_path="memories"),),
        )
        call = _call(
            "c1",
            "memory",
            command="create",
            path="/memories/note",
            file_text="espresso",
        )
        config = _config(
            FakeTurn(tool_calls=(call,)),
            FakeTurn(content="Noted: espresso, filed."),
            memory=memory,
        )
        app = _app(config)
        async with app.run_test() as pilot:
            await send(pilot, "remember espresso")
            text = _text(app)
        asked_at = text.index("> remember espresso")
        call_at = text.index("→ memory(")
        write_at = text.index("memory_write create /memories/note v1")
        reply_at = text.index("Noted: espresso, filed.")
        assert asked_at < call_at < write_at < reply_at
        assert len(await FileStore(tmp_path / "home").read_turns("t1")) == 1

    async def test_typed_keys_send_and_the_receipt_prices_the_turn(self) -> None:
        app = _app(_config(FakeTurn(content="The answer is forty-two.")))
        async with app.run_test() as pilot:
            await pilot.press("h", "i", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = _text(app)
            footer = plain(app.query_one("#footer", Static).content)
            assert app.query_one(Prompt).text == ""
            assert not app.query_one(Working).display
        assert "> hi" in text and "The answer is forty-two." in text
        assert "fake/fake" in text and "Response time" in text
        assert "$" in text and "$" in footer  # Model.FAKE is priced

    async def test_each_result_lands_on_the_call_it_answers(self) -> None:
        """EC-17: parallel calls finish in any order; here the first call
        waits for the second, and each line still holds its own result."""
        second_done = asyncio.Event()

        @Tool(name="slow", description="Waits for fast.")
        async def slow() -> ToolResult[str]:
            await second_done.wait()
            return ToolResult.ok("slow-result")

        @Tool(name="fast", description="Returns at once.")
        async def fast() -> ToolResult[str]:
            second_done.set()
            return ToolResult.ok("fast-result")

        config = _config(
            FakeTurn(tool_calls=(_call("c1", "slow"), _call("c2", "fast"))),
            FakeTurn(content="both done"),
            tools=(slow, fast),
        )
        app = _app(config)
        async with app.run_test() as pilot:
            await send(pilot, "go")
            first, second = (plain(call.content) for call in app.query(ToolCall))
        assert "slow(" in first and "slow-result" in first
        assert "fast(" in second and "fast-result" in second


@pytest.mark.unit
class TestAToolCallFolds:
    """A call is one line and its result one more, until it is asked for."""

    async def _mounted(self, host: _Host, call: ToolCall) -> ToolCall:
        await host.query_one("#transcript").mount(call)
        return call

    async def test_a_long_result_is_one_line_until_expanded(self) -> None:
        body = "\n".join(f"line {n}" for n in range(1, 401))
        async with _view() as (_, host):
            call = await self._mounted(host, ToolCall("read", {"path": "/notes"}))
            assert "… running" in plain(call.content)
            call.finish(True, body)
            folded = plain(call.content)
            assert folded.splitlines() == [
                "→ read(path='/notes')",
                "  ← line 1  (+399 lines)",
            ]
            call.on_click()
            assert "line 300" in plain(call.content)
            call.on_click()
            assert plain(call.content) == folded

    async def test_an_expanded_result_stops_at_the_ceiling(self) -> None:
        body = "\n".join(f"line {n}" for n in range(1, 701))
        async with _view() as (_, host):
            call = await self._mounted(host, ToolCall("read", {}))
            call.finish(True, body)
            call.fold(expanded=True)
            text = plain(call.content)
        assert "line 500" in text and "line 501" not in text
        assert "200 more lines" in text

    async def test_the_arguments_are_whole_once_expanded(self) -> None:
        async with _view() as (_, host):
            call = await self._mounted(host, ToolCall("say", {"text": "x" * 40}))
            assert "x" * 31 not in plain(call.content)
            call.fold(expanded=True)
            assert "x" * 40 in plain(call.content)

    async def test_a_failure_is_shown_and_brackets_are_data(self) -> None:
        async with _view() as (_, host):
            call = await self._mounted(host, ToolCall("tool [green]", {"k": "[bold]"}))
            call.finish(False, "no row [red]")
            text = plain(call.content)
        for literal in ("tool [green]", "[bold]", "no row [red]"):
            assert literal in text

    async def test_ctrl_o_unfolds_every_call_and_folds_them_back(self) -> None:
        @Tool(name="read", description="Reads.")
        async def read() -> ToolResult[str]:
            return ToolResult.ok("one\ntwo\nthree")

        config = _config(
            FakeTurn(tool_calls=(_call("c1", "read"), _call("c2", "read"))),
            FakeTurn(content="done"),
            tools=(read,),
        )
        app = _app(config)
        async with app.run_test() as pilot:
            await send(pilot, "go")
            assert "three" not in _text(app)
            await pilot.press("ctrl+o")
            assert _text(app).count("three") == 2
            await pilot.press("ctrl+o")
            assert "three" not in _text(app)


@pytest.mark.unit
class TestTheFrames:
    async def test_progress_names_how_long_the_call_has_run(self) -> None:
        async with _view() as (view, host):
            await view.render(ToolCallEvent(id="c1", name="crawl", arguments={}))
            await view.render(ToolProgressEvent(tool_call_id="c1", elapsed_ms=1200))
            assert "… running 1200 ms" in _text(host)

    async def test_reasoning_grows_in_one_block(self) -> None:
        async with _view() as (view, host):
            await view.render(ReasoningEvent(reasoning="weighing "))
            await view.render(ReasoningEvent(reasoning="it"))
            assert "weighing it" in _text(host)
            assert len(host.query(".thought")) == 1

    async def test_a_blocked_turn_says_why(self) -> None:
        async with _view() as (view, host):
            await view.render(BlockedEvent(rationale="policy P1"))
            text = _text(host)
        assert "Output blocked" in text and "policy P1" in text


@pytest.mark.unit
class TestTheBlockingPath:
    """The finished turn through the same widgets, when it cannot stream."""

    async def _render(self, response: AgentResponse) -> str:
        async with _view() as (view, host):
            await view.response(response)
            await view.close()
            return _text(host)

    async def test_output_guardrails_take_it(self) -> None:
        app = _app(_config(FakeTurn(content="ok")), streamed=False)
        async with app.run_test() as pilot:
            await send(pilot, "hi")
            text = _text(app)
        assert "ok" in text and "Response time" in text

    async def test_each_call_beside_the_result_it_answers(self) -> None:
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="done", reasoning="hmm"),
            tool_calls_made=(_call("a", "greet", q=1), _call("b", "lookup", q=1)),
            tool_results=(ToolResult.ok("Hello ada"), ToolResult.fail("no row")),
        )
        text = await self._render(response)
        order = [text.index(s) for s in ("greet(", "Hello ada", "lookup(", "no row")]
        assert order == sorted(order)
        assert "hmm" in text and "done" in text and "Response time" in text

    @pytest.mark.parametrize(("safe", "verdict"), [(True, "safe"), (False, "flagged")])
    async def test_the_guards_verdict(self, safe: bool, verdict: str) -> None:
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="fine"),
            guardrail_result=GuardrailResult(
                safe=safe,
                output_policy=PolicyResult(safe=safe, rationale="policy P2"),
            ),
        )
        text = await self._render(response)
        assert verdict in text and "fine" in text
        assert ("policy P2" in text) is not safe

    @pytest.mark.parametrize(
        ("where", "label"), [("input", "Input blocked"), ("output", "Output blocked")]
    )
    async def test_a_blocked_turn_shows_why_and_nothing_else(
        self, where: Literal["input", "output"], label: str
    ) -> None:
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="never shown"),
            blocked=True,
            guardrail_result=GuardrailResult(
                safe=False,
                flagged_at=where,
                input_policy=PolicyResult(safe=False, rationale="policy P1"),
            ),
        )
        text = await self._render(response)
        assert label in text and "policy P1" in text
        assert "never shown" not in text


@pytest.mark.unit
class TestTheSession:
    async def test_a_failed_turn_is_shown_and_the_session_goes_on(self) -> None:
        config = _config(
            FakeTurn(error=RuntimeError("provider down")), FakeTurn(content="two")
        )
        app = _app(config)
        async with app.run_test() as pilot:
            await send(pilot, "one")
            assert "Error:" in _text(app) and "provider down" in _text(app)
            await send(pilot, "again")
            assert "two" in _text(app)

    @pytest.mark.parametrize("key", ["escape", "ctrl+c"])
    async def test_an_interrupt_stops_the_turn_and_nothing_is_saved(
        self, tmp_path: Path, key: str
    ) -> None:
        @Tool(name="hang", description="Never returns.")
        async def hang() -> ToolResult[str]:
            await asyncio.Event().wait()
            return ToolResult.ok("never")

        config = _config(
            FakeTurn(tool_calls=(_call("c1", "hang"),)),
            FakeTurn(content="back"),
            tools=(hang,),
        )
        app = _app(config)
        store = FileStore(tmp_path / "home")
        async with app.run_test() as pilot:
            app.query_one(Prompt).text = "go"
            await pilot.press("enter")
            await _until(pilot, lambda: bool(app.query(ToolCall)))
            assert app.query_one(Working).display
            await pilot.press(key)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "interrupted: the turn was not saved" in _text(app)
            assert not app.query_one(Working).display
            assert await store.read_turns("t1") == ()
            await send(pilot, "and now")  # the send lock was let go
            assert "back" in _text(app)
        assert len(await store.read_turns("t1")) == 1

    async def test_a_second_send_waits_for_the_running_turn(self) -> None:
        release = asyncio.Event()

        @Tool(name="wait", description="Waits.")
        async def wait() -> ToolResult[str]:
            await release.wait()
            return ToolResult.ok("released")

        config = _config(
            FakeTurn(tool_calls=(_call("c1", "wait"),)),
            FakeTurn(content="done"),
            tools=(wait,),
        )
        app = _app(config)
        async with app.run_test() as pilot:
            prompt = app.query_one(Prompt)
            prompt.text = "go"
            await pilot.press("enter")
            await _until(pilot, lambda: bool(app.query(ToolCall)))
            prompt.text = "too soon"
            await pilot.press("enter")
            await pilot.pause()
            assert prompt.text == "too soon" and "> too soon" not in _text(app)
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "done" in _text(app)

    async def test_leaving_mid_turn_lets_the_conversation_go(self) -> None:
        """The close reflects only under a free send lock (§15)."""

        @Tool(name="hang", description="Never returns.")
        async def hang() -> ToolResult[str]:
            await asyncio.Event().wait()
            return ToolResult.ok("never")

        config = _config(
            FakeTurn(tool_calls=(_call("c1", "hang"),)),
            FakeTurn(content="back"),
            tools=(hang,),
        )
        app = _app(config)
        convo = app.session.convo
        async with app.run_test() as pilot:
            app.query_one(Prompt).text = "go"
            await pilot.press("enter")
            await _until(pilot, lambda: bool(app.query(ToolCall)))
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert not app.is_running
        response = await asyncio.wait_for(convo.send("still there?"), timeout=5)
        assert response.message.content == "back"

    @pytest.mark.parametrize("leave", ["/quit", "/exit", "/q"])
    async def test_the_exit_words_leave(self, leave: str) -> None:
        app = _app(_config())
        async with app.run_test() as pilot:
            await send(pilot, leave)
            assert app.return_code == 0 and not app.is_running

    async def test_ctrl_c_clears_the_prompt_then_asks_twice_to_leave(self) -> None:
        app = _app(_config())
        async with app.run_test() as pilot:
            prompt = app.query_one(Prompt)
            footer = app.query_one("#footer", Static)
            prompt.text = "half a thought"
            await pilot.press("ctrl+c")
            assert prompt.text == "" and "again" not in plain(footer.content)
            await pilot.press("ctrl+c")
            assert plain(footer.content).strip() == "ctrl+c again to quit"
            assert app.is_running
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert not app.is_running

    async def test_a_lone_ctrl_c_wears_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("neosian._cli.tui.app._AGAIN_SECONDS", 0.01)
        app = _app(_config())
        async with app.run_test() as pilot:
            footer = app.query_one("#footer", Static)
            await pilot.press("ctrl+c")
            await _until(pilot, lambda: "again" not in plain(footer.content))
            assert "fake/fake" in plain(footer.content) and app.is_running

    async def test_a_click_on_the_transcript_leaves_the_prompt_in_charge(
        self,
    ) -> None:
        """Without the focus, ctrl+c was Textual's own (its ctrl+q notice)
        and typed keys went nowhere."""
        app = _app(_config(FakeTurn(content="ok")))
        async with app.run_test() as pilot:
            await send(pilot, "hi")
            await pilot.click("#scroll")
            assert app.query_one(Prompt).has_focus
            await pilot.press("ctrl+c")
            footer = plain(app.query_one("#footer", Static).content)
            assert footer.strip() == "ctrl+c again to quit"

    async def test_a_sent_message_is_a_full_line(self) -> None:
        app = _app(_config(FakeTurn(content="ok")))
        async with app.run_test(size=(80, 24)) as pilot:
            await send(pilot, "hi")
            sent = app.query_one(".user", Static)
            assert sent.region.width == app.query_one("#transcript").region.width
            assert sent.styles.background.a == 1  # a bar, not bare text

    async def test_ctrl_c_copies_a_selection_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _app(_config())
        async with app.run_test() as pilot:
            monkeypatch.setattr(app.screen, "get_selected_text", lambda: "the answer")
            app.query_one(Prompt).text = "half a thought"
            await pilot.press("ctrl+c")
            assert app.clipboard == "the answer"
            assert app.query_one(Prompt).text == "half a thought" and app.is_running

    @pytest.mark.parametrize("key", ["shift+enter", "ctrl+j"])
    async def test_a_line_break_does_not_send(self, key: str) -> None:
        app = _app(_config())
        async with app.run_test() as pilot:
            await pilot.press("enter", "a", key, "b")  # an empty enter sends nothing
            assert app.query_one(Prompt).text == "a\nb"
            assert "> " not in _text(app)

    async def test_up_and_down_walk_what_was_sent_never_over_a_draft(self) -> None:
        app = _app(_config(FakeTurn(content="ok"), FakeTurn(content="ok")))
        async with app.run_test() as pilot:
            prompt = app.query_one(Prompt)
            await send(pilot, "first")
            await send(pilot, "second")
            await pilot.press("up")
            assert prompt.text == "second"
            await pilot.press("up", "up")  # the oldest stays
            assert prompt.text == "first"
            await pilot.press("down", "down")
            assert prompt.text == ""
            await pilot.press("d", "up")  # a draft is not replaced
            assert prompt.text == "d"

    async def test_the_page_keys_scroll_the_transcript(self) -> None:
        reply = "\n\n".join(f"paragraph {n}" for n in range(1, 60))
        app = _app(_config(FakeTurn(content=reply)))
        async with app.run_test(size=(80, 24)) as pilot:
            await send(pilot, "go")
            scroll = app.query_one("#scroll")
            bottom = scroll.scroll_y
            assert bottom == scroll.max_scroll_y > 0  # the view follows the turn
            await pilot.press("pageup")
            await pilot.pause()
            assert scroll.scroll_y < bottom
            await pilot.press("pagedown")
            await pilot.pause()
            assert scroll.scroll_y == bottom


@pytest.mark.unit
class TestTheOpening:
    _FACTS = (("Conversation", "s1"), ("Resume", "--resume s1"))

    def test_it_names_the_agent_the_way_out_and_the_facts(self) -> None:
        text = plain(Opening("my_agent", self._FACTS, "Resumed 2").render(100))
        assert "Agent: my_agent" in text and "/help" in text
        assert "Resumed 2" in text and "--resume s1" in text
        lines = text.splitlines()
        assert "▀█▀" in lines[5] and "██▄▄██" in lines[2]  # the mark, the wordmark
        assert "Agent: my_agent" in lines[4]  # under the wordmark, beside the mark
        assert len(lines) == 10  # the lockup (seven with a notice), a blank, two facts

    def test_a_narrow_terminal_drops_the_art(self) -> None:
        text = plain(Opening("my_agent", self._FACTS).render(60))
        assert "█" not in text and "Agent: my_agent" in text

    async def test_the_session_opens_on_it(self) -> None:
        app = _app(_config())
        async with app.run_test(size=(100, 30)):
            text = _text(app)
            assert app.query_one(Prompt).has_focus
        assert "Agent: probe" in text and "Conversation" in text and "t1" in text
