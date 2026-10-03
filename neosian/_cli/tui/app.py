"""The session app: a transcript, a prompt, one turn at a time.

A turn runs as a worker over `Conversation.send`; esc cancels it, which
closes the stream with its consumer, so the turn persists nothing and
the send lock is free for the next one (§9.5). The app draws in the
terminal's own colours (`NO_COLOR` keeps the layout and drops them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import ClassVar, Final

from rich.console import Group, RenderableType
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.theme import BUILTIN_THEMES
from textual.widgets import Static
from textual.worker import Worker, WorkerCancelled

from neosian._cli.display import field_table
from neosian._cli.tui.turn import TurnView
from neosian._cli.tui.widgets import Prompt, ToolCall, Working
from neosian._cli.ui import BRAND_ACCENT, BRAND_SUPPORT, load_header
from neosian._foundation.agent.lifetimes import closing
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.types import AnyModel, format_micro_usd

_ANSI: Final = BUILTIN_THEMES["ansi-dark"]  # the terminal's own background
_THEME: Final = replace(
    _ANSI,
    name="neosian",
    primary=BRAND_ACCENT,
    secondary=BRAND_SUPPORT,
    variables={
        **_ANSI.variables,
        **{f"markdown-h{level}-color": BRAND_ACCENT for level in range(1, 7)},
        "markdown-h2-text-style": "bold",
        "scrollbar": "ansi_bright_black",
        "scrollbar-hover": "ansi_white",
        "scrollbar-active": "ansi_white",
        "scrollbar-background": "ansi_default",
        "scrollbar-background-hover": "ansi_default",
        "scrollbar-background-active": "ansi_default",
    },
)
_PLACEHOLDER: Final = "enter sends, ctrl+j breaks the line"
_WORKING: Final = "esc interrupts"
_KEYS: Final = "ctrl+o tool calls   ctrl+d quit"
_BUSY: Final = "a turn is running: esc interrupts it"
_INTERRUPTED: Final = "interrupted: the turn was not saved"
_GUTTER: Final = 3  # the scroll's padding and its bar


@dataclass(frozen=True, slots=True)
class Opening:
    """What the session says before the first turn."""

    agent: str
    facts: Sequence[tuple[str, str]]
    notice: str | None = None

    def render(self, width: int) -> RenderableType:
        parts: list[RenderableType] = []
        header = load_header()
        if header.plain and max(map(len, header.plain.splitlines())) <= width:
            parts += [header, Text()]
        parts.append(
            Text(
                PlaygroundUI.AGENT_LOADED.format(name=self.agent),
                style=f"bold {BRAND_ACCENT}",
            )
        )
        parts.append(Text(PlaygroundUI.SESSION_START, style="dim"))
        if self.notice is not None:
            parts.append(Text(self.notice, style="dim"))
        parts += [Text(), field_table(self.facts)]
        return Group(*parts)


class SessionApp(App[None]):
    """One Conversation on the screen."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #scroll { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    #transcript { height: auto; min-height: 100%; }
    .user { margin-top: 1; color: $primary; text-style: bold; }
    .reply { padding: 0; }
    .blocked { border: round $error; padding: 0 1; }
    .error { color: $error; }
    #working { height: 1; padding: 0 1; display: none; }
    #prompt, #prompt:focus {
        height: auto; max-height: 10; border: round $primary; padding: 0 1;
    }
    #footer { height: 1; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "interrupt", show=False),
        Binding("ctrl+o", "unfold", show=False),
        Binding("pageup", "page(-1)", show=False, priority=True),
        Binding("pagedown", "page(1)", show=False, priority=True),
    ]

    def __init__(
        self,
        convo: Conversation,
        opening: Opening,
        *,
        model: AnyModel,
        title: Text,
        streamed: bool,
    ) -> None:
        super().__init__()
        self._convo = convo
        self._opening = opening
        self._model = model
        self._title = title
        self._streamed = streamed
        self._turn: Worker[None] | None = None
        self._spent: int | None = None

    def compose(self) -> ComposeResult:
        # An anchored scroll pins short content to the bottom; the
        # transcript's minimum height keeps the first turns at the top.
        with VerticalScroll(id="scroll"):
            yield Vertical(id="transcript")
        yield Working(_WORKING, id="working")
        yield Prompt(id="prompt", placeholder=_PLACEHOLDER)
        yield Static(id="footer")

    async def on_mount(self) -> None:
        self.register_theme(_THEME)
        self.theme = _THEME.name
        await self._transcript.mount(
            Static(self._opening.render(self.size.width - _GUTTER), id="opening")
        )
        self._scroll.anchor()
        self.query_one(Prompt).focus()
        self._footer()

    @property
    def _scroll(self) -> VerticalScroll:
        return self.query_one("#scroll", VerticalScroll)

    @property
    def _transcript(self) -> Vertical:
        return self.query_one("#transcript", Vertical)

    @property
    def _busy(self) -> bool:
        return self._turn is not None and self._turn.is_running

    def _footer(self) -> None:
        line = self._title.copy()
        if self._spent is not None:
            line.append(f"  {format_micro_usd(self._spent)}", style="dim")
        line.append(f"   {_KEYS}", style="dim")
        self.query_one("#footer", Static).update(line)

    @on(Prompt.Submitted)
    async def _submitted(self, event: Prompt.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return
        if text.lower() in PlaygroundUI.EXIT_COMMANDS:
            await self.action_quit()
        elif self._busy:
            self.notify(_BUSY)
        else:
            self.query_one(Prompt).clear()
            self._turn = self.run_worker(self._run(text), exit_on_error=False)

    async def _run(self, text: str) -> None:
        working = self.query_one(Working)
        await self._transcript.mount(Static(Text(f"> {text}"), classes="user"))
        self._scroll.anchor()
        view = TurnView(self._transcript, self._model, self._title)
        working.start()
        try:
            if self._streamed:
                stream = await self._convo.send(text, stream=True)
                async with closing(stream) as events:
                    async for event in events:
                        await view.render(event)
            else:
                await view.response(await self._convo.send(text))
        except asyncio.CancelledError:
            await view.note(Text(_INTERRUPTED, style="dim"), "interrupted")
            raise
        except Exception as exc:
            await view.note(Text(f"Error: {exc}"), "error")
        finally:
            await view.close()
            working.stop()
            if view.cost is not None:
                self._spent = (self._spent or 0) + view.cost
            self._footer()

    async def _stop(self) -> None:
        if self._turn is not None:
            self._turn.cancel()
            with suppress(WorkerCancelled):
                await self._turn.wait()

    def action_interrupt(self) -> None:
        if self._turn is not None:
            self._turn.cancel()

    async def action_cancel(self) -> None:
        """ctrl+c: copy a selection, else stop the turn, else clear the
        prompt, else leave."""
        prompt = self.query_one(Prompt)
        if self.screen.get_selected_text():
            self.screen.action_copy_text()
        elif self._busy:
            self.action_interrupt()
        elif prompt.text:
            prompt.clear()
        else:
            await self.action_quit()

    async def action_quit(self) -> None:
        """Leave once the turn has let go of the conversation: its close
        reflects only under a free send lock (§15)."""
        await self._stop()
        self.exit()

    def action_unfold(self) -> None:
        calls = list(self.query(ToolCall))
        expanded = any(not call.expanded for call in calls)
        for call in calls:
            call.fold(expanded=expanded)

    def action_page(self, direction: int) -> None:
        scroll = self._scroll
        scroll.scroll_relative(y=direction * scroll.size.height, animate=False)
