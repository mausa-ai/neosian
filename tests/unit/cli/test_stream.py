"""The playground's streamed turn (NC2): frames on the console as they
land, the blocking path kept for output guardrails. Zero keys."""

import asyncio
import io
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from neosian import AgentConfig, GuardrailMode, GuardrailsConfig, Model, PolicyBuilder
from neosian._cli.chat import open_chat, streams, turn_title
from neosian._cli.stream import _Renderer, stream_turn
from neosian._foundation.agent.events import (
    BlockedEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolProgressEvent,
)
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.types import ToolCallId, ToolFunction, ToolName
from neosian._foundation.tools.base import Tool, ToolResult

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


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=120, no_color=True, force_terminal=False), out


@pytest.mark.unit
class TestStreamTurn:
    async def test_tool_call_and_memory_write_land_before_the_reply(
        self, tmp_path: Path
    ) -> None:
        memory = MemoryConfig(
            store=FileStore(tmp_path / "mem"),
            mounts=(Mount(scope="user:demo", mount_path="memories"),),
        )
        call = ToolCall(
            id=ToolCallId("c1"),
            name=ToolName("memory"),
            arguments={
                "command": "create",
                "path": "/memories/note",
                "file_text": "espresso",
            },
        )
        config = _config(
            FakeTurn(tool_calls=(call,)),
            FakeTurn(content="Noted: espresso, filed."),
            memory=memory,
        )
        convo = open_chat(config, conversation_id="t1")
        console, out = _console()
        await stream_turn(
            console,
            convo,
            "remember espresso",
            model=Model.FAKE,
            title=turn_title(config),
        )
        text = out.getvalue()
        call_at = text.index("→ memory(")
        write_at = text.index("memory_write create /memories/note v1")
        reply_at = text.index("Noted: espresso, filed.")
        assert call_at < write_at < reply_at
        assert text.rstrip().endswith(turn_title(config).plain) or "fake/fake" in text
        assert (tmp_path / "mem").exists()

    async def test_deltas_arrive_whole_and_the_footer_prices_the_turn(self) -> None:
        config = _config(FakeTurn(content="The answer is forty-two."))
        convo = open_chat(config, conversation_id="t2")
        console, out = _console()
        await stream_turn(
            console, convo, "hi", model=Model.FAKE, title=turn_title(config)
        )
        text = out.getvalue()
        assert "The answer is forty-two." in text
        assert "fake/fake" in text
        assert "$" in text  # Model.FAKE is priced, so the footer carries µ$

    async def test_each_result_names_the_call_it_answers(self) -> None:
        """EC-17: parallel calls finish in any order; here the first call
        waits for the second, so its result lands last and still says whose
        it is."""
        second_done = asyncio.Event()

        @Tool(name="slow", description="Waits for fast.")
        async def slow() -> ToolResult[str]:
            await second_done.wait()
            return ToolResult.ok("slow-result")

        @Tool(name="fast", description="Returns at once.")
        async def fast() -> ToolResult[str]:
            second_done.set()
            return ToolResult.ok("fast-result")

        calls = (
            ToolCall(id=ToolCallId("c1"), name=ToolName("slow"), arguments={}),
            ToolCall(id=ToolCallId("c2"), name=ToolName("fast"), arguments={}),
        )
        config = _config(
            FakeTurn(tool_calls=calls),
            FakeTurn(content="both done"),
            tools=(slow, fast),
        )
        convo = open_chat(config, conversation_id="t3")
        console, out = _console()
        await stream_turn(
            console, convo, "go", model=Model.FAKE, title=turn_title(config)
        )
        text = out.getvalue()
        assert text.index("← fast: fast-result") < text.index("← slow: slow-result")


@pytest.mark.unit
class TestTheRenderer:
    def _render(self, *events: object) -> str:
        console, out = _console()
        renderer = _Renderer(console, Model.FAKE, Text("fake/fake"))
        for event in events:
            renderer.render(event)  # type: ignore[arg-type]
        return out.getvalue()

    def test_progress_names_the_call_still_running(self) -> None:
        text = self._render(
            ToolCallEvent(id="c1", name="crawl", arguments={}),
            ToolProgressEvent(tool_call_id="c1", elapsed_ms=1200),
        )
        assert "crawl still running 1200 ms" in text

    def test_reasoning_streams_beside_the_text(self) -> None:
        assert "weighing it" in self._render(ReasoningEvent(reasoning="weighing it"))

    def test_a_blocked_turn_says_why(self) -> None:
        text = self._render(BlockedEvent(rationale="policy P1"))
        assert "Output blocked" in text and "policy P1" in text


@pytest.mark.unit
class TestStreams:
    def test_plain_and_input_guarded_configs_stream(self) -> None:
        assert streams(_config()) is True
        guarded = AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            guardrails=GuardrailsConfig(
                input_mode=GuardrailMode.POLICY_ONLY, input_policy=PolicyBuilder.test()
            ),
        )
        assert streams(guarded) is True

    def test_output_guardrails_take_the_blocking_path(self) -> None:
        guarded = AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            guardrails=GuardrailsConfig(
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=PolicyBuilder.test(),
            ),
        )
        assert streams(guarded) is False
