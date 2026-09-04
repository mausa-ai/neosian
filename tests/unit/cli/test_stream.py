"""The playground's streamed turn (NC2): frames on the console as they
land, the blocking path kept for output guardrails. Zero keys."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from neosian import AgentConfig, GuardrailMode, GuardrailsConfig, Model, PolicyBuilder
from neosian._cli.chat import open_chat, streams, turn_title
from neosian._cli.stream import stream_turn
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.types import ToolCallId, ToolName

_SYSTEM = "You are a test agent."


def _config(*turns: FakeTurn, memory: MemoryConfig | None = None) -> AgentConfig:
    fake = FakeClient(FakeScript(turns=turns, chunk_chars=4))
    return AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        memory=memory,
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
