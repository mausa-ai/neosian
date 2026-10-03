"""The session app: a transcript, a prompt, one turn at a time.

A turn runs as a worker over `Conversation.send`; esc cancels it, which
closes the stream with its consumer, so the turn persists nothing and
the send lock is free for the next one (§9.5). A line that begins with
`/` is one of the session's commands (`tui/commands.py`), run as the same
worker. The app owns which Conversation it is on: `reopen` closes the
current one and opens another, the same id on another model or another
id altogether. It draws in the terminal's own colours (`NO_COLOR` keeps
the layout and drops them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from typing import ClassVar, Final

from rich.console import RenderableType
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static
from textual.worker import Worker, WorkerCancelled

from neosian._cli.chat import Session, open_session
from neosian._cli.tui.asks import Confirm, Pick, Secret
from neosian._cli.tui.commands import BY_NAME, MENU, Command
from neosian._cli.tui.theme import THEME
from neosian._cli.tui.turn import TurnView, replay
from neosian._cli.tui.widgets import Menu, Prompt, ToolCall, Working
from neosian._foundation.agent.lifetimes import closing
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.types import AgentConfig, AnyModel, format_micro_usd

_PLACEHOLDER: Final = "enter sends, ctrl+j breaks the line, / for commands"
_WORKING: Final = "esc interrupts"
_KEYS: Final = "ctrl+o tool calls   /help   ctrl+d quit"
_AGAIN: Final = "ctrl+c again to quit"
_AGAIN_SECONDS: Final = 2.0  # how long the first ctrl+c stays armed
_BUSY: Final = "a turn is running: esc interrupts it"
_INTERRUPTED: Final = "interrupted: the turn was not saved"
_UNKNOWN: Final = "no command /{name}: /help lists them"
_GUTTER: Final = 3  # the scroll's padding and its bar


class SessionApp(App[None]):
    """One Conversation on the screen, and the way to another."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    /* A sent message is a full line: the accent, dimmed, behind it. */
    .user {
        margin: 1 0; padding: 0 1; background: #34342a;
        color: $primary; text-style: bold;
    }
    #scroll { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    #transcript { height: auto; min-height: 100%; }
    .reply { padding: 0; margin-top: 1; }
    .blocked { border: round $error; padding: 0 1; }
    .error { color: $error; }
    #working { height: 1; padding: 0 1; display: none; }
    #menu { height: auto; padding: 0 1; display: none; }
    #prompt, #prompt:focus {
        height: auto; max-height: 10; border: round $primary; padding: 0 1;
    }
    #footer { height: 1; padding: 0 1; }
    .notice { margin-bottom: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", show=False),  # wherever the focus is
        Binding("escape", "interrupt", show=False),
        Binding("ctrl+o", "unfold", show=False),
        Binding("pageup", "page(-1)", show=False, priority=True),
        Binding("pagedown", "page(1)", show=False, priority=True),
    ]

    def __init__(
        self,
        session: Session,
        *,
        config: AgentConfig,
        agent: str,
        servers: Sequence[McpServer] = (),
    ) -> None:
        super().__init__()
        self.session = session
        self.config = config
        self.agent = agent
        self._servers = servers
        self._turn: Worker[None] | None = None
        self._spent: int | None = None
        self._leaving = False  # a first ctrl+c, waiting for the second

    def compose(self) -> ComposeResult:
        # An anchored scroll pins short content to the bottom; the
        # transcript's minimum height keeps the first turns at the top.
        with VerticalScroll(id="scroll", can_focus=False):  # the prompt keeps it
            yield Vertical(id="transcript")
        yield Working(_WORKING, id="working")
        menu = Menu(MENU, id="menu")
        yield menu
        yield Prompt(menu, id="prompt", placeholder=_PLACEHOLDER)
        yield Static(id="footer")

    async def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        await self._open()
        self.query_one(Prompt).focus()
        self._footer()

    async def _open(self) -> None:
        """The opening, then the last turns of a conversation that has any."""
        opening = self.session.opening.render(self.size.width - _GUTTER)
        await self._transcript.mount(Static(opening, id="opening"))
        await replay(self._transcript, self.session.convo.messages)
        self._scroll.anchor()

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
        if self._leaving:
            self.query_one("#footer", Static).update(Text(_AGAIN, style="bold"))
            return
        line = self.session.title.copy()
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
            self.query_one(Prompt).remember(text)
            await self._transcript.mount(Static(Text(f"> {text}"), classes="user"))
            self._scroll.anchor()
            work = self._command(text[1:]) if text.startswith("/") else self._run(text)
            self._turn = self.run_worker(work, exit_on_error=False)

    async def _command(self, line: str) -> None:
        name, _, argument = line.partition(" ")
        command: Command | None = BY_NAME.get(name.lower())
        try:
            if command is None:
                await self.say(Text(_UNKNOWN.format(name=name)), "error")
            else:
                await command.run(self, argument.strip())
        except Exception as exc:
            await self.say(Text(f"Error: {exc}"), "error")
        finally:
            self._footer()

    async def _run(self, text: str) -> None:
        working = self.query_one(Working)
        session = self.session
        view = TurnView(self._transcript, session.model, session.title)
        working.start()
        try:
            if session.streamed:
                stream = await session.convo.send(text, stream=True)
                async with closing(stream) as events:
                    async for event in events:
                        await view.render(event)
            else:
                await view.response(await session.convo.send(text))
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

    async def confirm(self, command: str) -> bool:
        """Ask the human whether `command` may run; the turn waits."""
        approved: bool = await self.push_screen_wait(Confirm(command))
        return approved

    async def pick(self, title: str, options: Sequence[str]) -> int | None:
        """One of `options` by its index, or None when the human backs out."""
        choice: int | None = await self.push_screen_wait(Pick(title, options))
        return choice

    async def secret(self, title: str) -> str | None:
        """A value typed masked: it reaches the caller and nothing else,
        never the transcript, a turn or the model."""
        value: str | None = await self.push_screen_wait(Secret(title))
        return value

    async def say(self, said: RenderableType, classes: str = "notice") -> None:
        await self._transcript.mount(Static(said, classes=classes))

    async def reopen(
        self,
        *,
        model: AnyModel | None = None,
        conversation_id: str | None = None,
        resumed: bool = True,
    ) -> None:
        """Close this Conversation (it reflects, §15) and open another:
        the same id on `model` or on a fresh client, with the transcript
        kept; or `conversation_id`, whose own turns replace it."""
        config = self.config if model is None else replace(self.config, model=model)
        current = self.session.convo
        await current.aclose()
        self.session = await open_session(
            config,
            self.agent,
            conversation_id=conversation_id or current.conversation_id,
            resumed=resumed,
            servers=self._servers,
        )
        self.config = config
        if conversation_id is not None:
            await self._transcript.remove_children()
            await self._open()

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
        prompt, else leave on the second press."""
        prompt = self.query_one(Prompt)
        if self.screen.get_selected_text():
            self.screen.action_copy_text()
        elif self._busy:
            self.action_interrupt()
        elif prompt.text:
            prompt.clear()
        elif self._leaving:
            await self.action_quit()
        else:
            self._arm(leaving=True)
            self.set_timer(_AGAIN_SECONDS, self._arm)

    def _arm(self, *, leaving: bool = False) -> None:
        self._leaving = leaving
        self._footer()

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
