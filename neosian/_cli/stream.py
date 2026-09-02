"""The playground's streamed turn: frames rendered as they arrive.

Tool calls, their results and `memory_write` receipts print the moment
the agent emits them (the tour's finding — a write is visible while the
turn still runs); text and reasoning deltas print as they land; `done`
closes with the model, the elapsed time and the turn's µ$ when the model
is priced. The event vocabulary is DESIGN §6; `Conversation.send(...,
stream=True)` persists the turn before its terminal frame, so nothing
rendered here is lost on a crash after it.
"""

import time

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.text import Text

from neosian._cli.ui import format_args, format_elapsed_time
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
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.types import AnyModel, format_micro_usd

_BLOCKED = "blocked"
_MEMORY_WRITE = "memory_write"
_STILL_RUNNING = "still running"


class _Renderer:
    """One turn's console state: whether a text line is open."""

    def __init__(self, console: Console, model: AnyModel, title: Text) -> None:
        self._console = console
        self._model = model
        self._title = title
        self._started = time.perf_counter()
        self._midline = False

    def _line(self, renderable: RenderableType) -> None:
        if self._midline:
            self._console.print()
            self._midline = False
        self._console.print(renderable)

    def _delta(self, text: str, style: str) -> None:
        self._console.print(text, end="", style=style, markup=False, highlight=False)
        self._midline = True

    def render(self, event: AgentEvent) -> None:
        if isinstance(event, ContentEvent):
            self._delta(event.content, "")
        elif isinstance(event, ReasoningEvent):
            self._delta(event.reasoning, "dim")
        elif isinstance(event, ToolCallEvent):
            text = Text("→ ", style="dim")
            text.append(event.name, style="yellow")
            text.append(f"({format_args(dict(event.arguments))})", style="dim")
            self._line(text)
        elif isinstance(event, ToolResultEvent):
            text = Text("  ← ", style="dim")
            if event.success:
                text.append(str(event.data), style="green")
            else:
                text.append(str(event.error), style="red")
            self._line(text)
        elif isinstance(event, ToolProgressEvent):
            self._line(Text(f"  … {_STILL_RUNNING} {event.elapsed_ms} ms", style="dim"))
        elif isinstance(event, MemoryWriteEvent):
            text = Text("  ✎ ", style="dim")
            text.append(_MEMORY_WRITE, style="magenta")
            text.append(f" {event.command} {event.path} v{event.version}")
            if event.previous_path is not None:
                text.append(f" (was {event.previous_path})", style="dim")
            self._line(text)
        elif isinstance(event, BlockedEvent):
            text = Text(PlaygroundUI.GUARDRAIL_OUTPUT_BLOCKED, style="bold red")
            if event.rationale:
                text.append("\n")
                text.append(
                    PlaygroundUI.GUARDRAIL_RATIONALE.format(rationale=event.rationale),
                    style="dim",
                )
            self._line(
                Panel(
                    text, title=PlaygroundUI.GUARDRAIL_BLOCKED_LABEL, border_style="red"
                )
            )
        elif isinstance(event, DoneEvent):
            footer = Text()
            footer.append_text(self._title)
            elapsed = format_elapsed_time(time.perf_counter() - self._started)
            footer.append(f"  {elapsed}", style="dim")
            cost = event.usage.cost_micro_usd(self._model) if event.usage else None
            if cost is not None:
                footer.append(f"  {format_micro_usd(cost)}", style="dim")
            self._line(footer)


async def stream_turn(
    console: Console,
    convo: Conversation,
    message: str,
    *,
    model: AnyModel,
    title: Text,
) -> None:
    """Send one turn streamed and render every frame. The stream is always
    drained — a raising generator is closed by the raise — so the
    conversation can close after it (§9.5)."""
    renderer = _Renderer(console, model, title)
    async for event in await convo.send(message, stream=True):
        renderer.render(event)
