"""One turn in the transcript: its widgets, mounted in event order.

The streamed path feeds `render` the §6 frames as they arrive: text and
reasoning grow in place, a tool call lands as one folded line and takes
its result when it comes, a `memory_write` receipt shows while the turn
still runs, and the terminal frame closes with the model, the elapsed
time and the turn's µ$ when the model is priced. The blocking path
(output guardrails) hands `response` the finished turn and gets the same
widgets.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Final

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownStream

from neosian._cli.tui.widgets import ToolCall
from neosian._cli.ui import BRAND_ACCENT, turn_footer
from neosian._foundation.agent.events import (
    AgentEvent,
    BlockedEvent,
    ContentEvent,
    DoneEvent,
    MemoryWriteEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import Message, Role, Usage, text_of
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.types import AnyModel

_MEMORY_WRITE = "memory_write"
_REPLAYED_TURNS: Final = 10  # a resumed conversation shows this many of its last
_EARLIER: Final = (
    "… {count} earlier turns are not drawn: `search_history` and "
    "`recall_turn` reach them"
)


def _blocked(headline: str, rationale: str | None) -> Text:
    text = Text(headline, style="bold red")
    if rationale:
        text.append("\n")
        text.append(
            PlaygroundUI.GUARDRAIL_RATIONALE.format(rationale=rationale), style="dim"
        )
    return text


class TurnView:
    """The widgets of one turn and the spend it reported."""

    def __init__(self, transcript: Widget, model: AnyModel, title: Text):
        self._transcript = transcript
        self._model = model
        self._title = title
        self._started = time.perf_counter()
        self._reply: MarkdownStream | None = None
        self._thought: tuple[Static, str] | None = None
        # Parallel calls finish in any order: a result or a progress frame
        # names its call by the frame's id (EC-17).
        self._calls: dict[str, ToolCall] = {}
        self.cost: int | None = None

    async def note(self, text: Text, classes: str) -> None:
        await self._mount(Static(text, classes=classes))

    async def close(self) -> None:
        """End the open text block: the next frame starts its own."""
        if self._reply is not None:
            await self._reply.stop()
            self._reply = None
        self._thought = None

    async def render(self, event: AgentEvent) -> None:
        match event:
            case ContentEvent():
                await self._say(event.content)
            case ReasoningEvent():
                await self._think(event.reasoning)
            case ToolCallEvent():
                self._calls[event.id] = ToolCall(event.name, event.arguments)
                await self._mount(self._calls[event.id])
            case ToolResultEvent():
                if call := self._calls.get(event.tool_call_id):
                    call.finish(
                        event.success, event.data if event.success else event.error
                    )
            case ToolProgressEvent():
                if call := self._calls.get(event.tool_call_id):
                    call.progress(event.elapsed_ms)
            case MemoryWriteEvent():
                text = Text("  ✎ ", style="dim")
                text.append(_MEMORY_WRITE, style=BRAND_ACCENT)
                text.append(f" {event.command} {event.path} v{event.version}")
                if event.previous_path is not None:
                    text.append(f" (was {event.previous_path})", style="dim")
                await self.note(text, "receipt")
            case BlockedEvent():
                await self.note(
                    _blocked(PlaygroundUI.GUARDRAIL_OUTPUT_BLOCKED, event.rationale),
                    "blocked",
                )
                await self._receipt(event.usage)
            case DoneEvent():
                await self._receipt(event.usage)
            case _:
                pass

    async def response(self, response: AgentResponse) -> None:
        guard = response.guardrail_result
        if guard is not None:
            verdict = Text("  guard: ", style="dim")
            verdict.append(*(("safe", "green") if guard.safe else ("flagged", "red")))
            if not guard.safe and guard.policy_rationale:
                verdict.append(f" ({guard.policy_rationale})", style="yellow")
            await self.note(verdict, "receipt")
        if response.blocked and guard is not None:
            headline = (
                PlaygroundUI.GUARDRAIL_INPUT_BLOCKED
                if guard.flagged_at == "input"
                else PlaygroundUI.GUARDRAIL_OUTPUT_BLOCKED
            )
            await self.note(_blocked(headline, guard.policy_rationale), "blocked")
            await self._receipt(response.usage)
            return  # a blocked turn persists nothing (§9.5.5): this is its log
        for index, made in enumerate(response.tool_calls_made):
            call = ToolCall(made.name, made.arguments)
            await self._mount(call)
            if index < len(response.tool_results):
                result = response.tool_results[index]
                call.finish(
                    result.success, result.data if result.success else result.error
                )
        if response.message.reasoning:
            await self._think(response.message.reasoning)
        if text := text_of(response.message):
            await self._say(text)
        await self._receipt(response.usage)

    async def _mount(self, widget: Widget) -> None:
        await self.close()
        await self._transcript.mount(widget)

    async def _say(self, delta: str) -> None:
        if self._reply is None:
            reply = Markdown(classes="reply")
            await self._mount(reply)
            self._reply = Markdown.get_stream(reply)
        await self._reply.write(delta)

    async def _think(self, delta: str) -> None:
        if self._thought is None:
            thought = Static(classes="thought")
            await self._mount(thought)
            self._thought = (thought, "")
        widget, text = self._thought
        self._thought = (widget, text + delta)
        widget.update(Text(text + delta, style="dim"))

    async def _receipt(self, usage: Usage | None) -> None:
        self.cost = usage.cost_micro_usd(self._model) if usage else None
        elapsed = time.perf_counter() - self._started
        await self.note(turn_footer(self._title, elapsed, self.cost), "receipt")


def _outcome(envelope: str) -> tuple[bool, object]:
    """A stored tool result back to what the live frame carried."""
    try:
        result = json.loads(envelope)
    except ValueError:
        return True, envelope
    if not isinstance(result, dict) or "success" not in result:
        return True, envelope
    return bool(result["success"]), result.get("data" if result["success"] else "error")


async def replay(transcript: Widget, messages: Sequence[Message]) -> None:
    """A resumed conversation's last turns, drawn as they were live: what
    was asked, the reply, each call folded with its result."""
    asked = [at for at, message in enumerate(messages) if message.role is Role.USER]
    earlier = max(len(asked) - _REPLAYED_TURNS, 0)
    widgets: list[Widget] = []
    if earlier:
        widgets.append(Static(Text(_EARLIER.format(count=earlier), style="dim")))
    calls: dict[str, ToolCall] = {}
    for message in messages[asked[earlier] :] if asked else ():
        text = text_of(message)
        if message.role is Role.USER:
            widgets.append(Static(Text(f"> {text}"), classes="user"))
        elif message.role is Role.TOOL:
            if call := calls.get(str(message.tool_call_id)):
                call.finish(*_outcome(text))
        elif message.role is Role.ASSISTANT:
            if text:
                widgets.append(Markdown(text, classes="reply"))
            for made in message.tool_calls:
                calls[str(made.id)] = ToolCall(made.name, made.arguments)
                widgets.append(calls[str(made.id)])
    if widgets:
        await transcript.mount(*widgets)
