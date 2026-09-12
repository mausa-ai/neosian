"""The cap on the model's copy of a tool result (DESIGN §3, NC9 #228).

The cap exists to keep one runaway tool from eating the window, so what
matters is that it bounds the envelope the model reads, that what it
hands back is still valid JSON with its verdict and its repair hint
intact, and that nothing else in the run loses the whole result.
"""

import json

import pytest

from neosian import (
    Agent,
    AgentConfig,
    Model,
    Tool,
    ToolResult,
    ToolResultEvent,
    Usage,
)
from neosian._foundation.agent.tool_exec import format_tool_result
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian._foundation.tools.result import FAILED_ENVELOPE_PREFIX

_USER = [Message(role=Role.USER, content="Hi")]
_USAGE = Usage(input_tokens=10, output_tokens=1)
_BIG = "x" * 5_000


@Tool(name="dump", description="Return a great deal of text")
async def dump() -> ToolResult[str]:
    return ToolResult.ok(_BIG)


@pytest.mark.unit
class TestTheCapBoundsTheEnvelope:
    def test_under_the_cap_is_byte_identical(self) -> None:
        result = ToolResult.ok("small")
        assert format_tool_result(result, 1_000) == result.to_json()

    def test_exactly_at_the_cap_is_untouched(self) -> None:
        result = ToolResult.ok("small")
        assert format_tool_result(result, len(result.to_json())) == result.to_json()

    def test_over_the_cap_is_clipped_to_it(self) -> None:
        out = format_tool_result(ToolResult.ok(_BIG), 500)
        assert len(out) <= 500
        assert out != ToolResult.ok(_BIG).to_json()

    def test_no_cap_sends_the_whole_thing(self) -> None:
        result = ToolResult.ok(_BIG)
        assert format_tool_result(result, None) == result.to_json()

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("ascii", "x" * 5_000),
            ("cjk", "日本語のテキスト" * 400),
            ("emoji", "🎉" * 1_000),
        ],
    )
    def test_the_budget_holds_whatever_the_escaping_costs(
        self, label: str, payload: str
    ) -> None:
        """One source character serializes to one escaped character or six,
        so the cap is measured on the envelope, never computed from the
        payload's length."""
        out = format_tool_result(ToolResult.ok(payload), 400)
        assert len(out) <= 400, label
        # And it packs the budget rather than collapsing to nothing.
        assert len(json.loads(out)["data"]) > 10, label


@pytest.mark.unit
class TestWhatSurvivesAClip:
    def test_the_envelope_is_still_json(self) -> None:
        payload = json.loads(format_tool_result(ToolResult.ok(_BIG), 500))
        assert payload["success"] is True
        assert payload["data"].endswith("chars]")

    def test_the_marker_names_what_was_dropped(self) -> None:
        out = format_tool_result(ToolResult.ok(_BIG), 500)
        kept = json.loads(out)["data"]
        dropped = len(_BIG) - len(kept.split("\n…")[0])
        assert f"[truncated {dropped} chars]" in kept

    def test_a_failure_keeps_its_prefix_and_its_code(self) -> None:
        """Anthropic reads `is_error` off the prefix, and the model reads
        the code to repair the call — a clip may cost neither."""
        out = format_tool_result(
            ToolResult.fail("y" * 5_000, code="tool_execution_failed"), 500
        )
        assert out.startswith(FAILED_ENVELOPE_PREFIX)
        assert json.loads(out)["code"] == "tool_execution_failed"
        assert len(out) <= 500

    def test_the_system_reminder_survives(self) -> None:
        out = format_tool_result(
            ToolResult.ok(_BIG, system_reminder="use fewer rows next time"), 500
        )
        assert json.loads(out)["system_reminder"] == "use fewer rows next time"

    def test_a_limit_under_the_envelope_s_floor_empties_the_payload(self) -> None:
        """The cap bounds the payload, which is what grows; it never breaks
        the JSON or drops the hint to make a pathological number fit."""
        out = format_tool_result(ToolResult.ok(_BIG, system_reminder="hint"), 20)
        payload = json.loads(out)
        assert payload["system_reminder"] == "hint"
        assert payload["data"].startswith("\n…")


@pytest.mark.unit
class TestTheRunSeesTheWholeResult:
    async def test_the_model_is_capped_and_the_wire_is_not(self) -> None:
        """The marker protects the context window, not the host: the
        streamed frame still carries everything the tool returned."""
        call = ToolCall(id=ToolCallId("c1"), name=ToolName("dump"), arguments={})
        client = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(tool_calls=(call,), usage=_USAGE),
                    FakeTurn(content="done", usage=_USAGE),
                )
            )
        )
        agent = Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.FAKE,
                tools=[dump],
                enable_todo=False,
                client_factory=lambda _m: client,
                max_tool_result_chars=500,
            )
        )
        events = [event async for event in await agent.run(_USER, stream=True)]

        (frame,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert frame.data == _BIG  # the host's copy, whole

        (tool_message,) = [m for m in client.calls[-1].messages if m.role == Role.TOOL]
        assert isinstance(tool_message.content, str)
        assert len(tool_message.content) <= 500  # the model's copy, capped
        assert "[truncated" in tool_message.content

    async def test_the_cap_is_on_by_default(self) -> None:
        assert AgentConfig(system_prompt="x").max_tool_result_chars == 32_000
