"""The session view's pieces: the prompt and its command menu, a tool
call that folds, the working line. Application data is literal `Text`,
never markup."""

from __future__ import annotations

import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from rich.console import Group, RenderableType
from rich.pretty import pretty_repr
from rich.text import Text
from textual import events, on
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Static, TextArea

from neosian._cli.ui import BRAND_ACCENT, format_args

_NEWLINE_KEYS: Final = ("shift+enter", "ctrl+j")
_HISTORY_KEYS: Final = {"up": -1, "down": 1}
_BODY_LINES: Final = 500  # an expanded result draws this many lines at most
_FRAMES: Final = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FRAME_SECONDS: Final = 0.1


class Menu(Static):
    """What a typed `/` may still become: a row per matching command, one
    of them chosen. It follows the prompt's text and is gone once the
    command is whole (a space) or matches nothing."""

    def __init__(self, rows: Sequence[tuple[str, str]], *, id: str) -> None:
        super().__init__(id=id)
        self._rows = rows
        self._matches: list[tuple[str, str]] = []
        self._at = 0

    @property
    def chosen(self) -> str | None:
        return self._matches[self._at][0] if self._matches else None

    def offer(self, typed: str) -> None:
        stem = typed[1:]
        naming = typed.startswith("/") and not any(map(str.isspace, stem))
        matches = [row for row in self._rows if naming and row[0].startswith(stem)]
        if matches != self._matches:
            self._matches, self._at = matches, 0
        self._draw()

    def move(self, step: int) -> None:
        self._at = (self._at + step) % len(self._matches)
        self._draw()

    def _draw(self) -> None:
        self.display = bool(self._matches)
        width = max((len(name) for name, _ in self._matches), default=0) + 3
        lines = Text()
        for at, (name, summary) in enumerate(self._matches):
            chosen = at == self._at
            lines.append("› " if chosen else "  ", style=BRAND_ACCENT)
            lines.append(
                f"/{name}".ljust(width), style=f"bold {BRAND_ACCENT}" if chosen else ""
            )
            lines.append(f"{summary}\n", style="" if chosen else "dim")
        lines.rstrip()
        self.update(lines)


class Prompt(TextArea):
    """Enter sends; ctrl+j breaks the line (shift+enter too, where the
    terminal reports it); up and down walk what this session sent, from
    an empty prompt or a recalled line, never over a draft. While the
    menu is open the keys are its own: up and down choose, tab completes
    the choice into the prompt, enter runs it, esc puts the menu away."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "app.cancel", show=False),
        Binding("ctrl+d", "app.quit", show=False),
    ]

    @dataclass
    class Submitted(Message):
        text: str

    def __init__(self, menu: Menu, *, id: str, placeholder: str) -> None:
        super().__init__(id=id, placeholder=placeholder)
        self._menu = menu
        self._sent: list[str] = []
        self._at = 0  # len(_sent) is the empty line after the newest

    def remember(self, sent: str) -> None:
        """Take a sent line into the history and clear the prompt."""
        self._sent.append(sent)
        self._at = len(self._sent)
        self.clear()

    def _recall(self, step: int) -> bool:
        at = self._at + step
        recalled = self._at < len(self._sent) and self.text == self._sent[self._at]
        if not 0 <= at <= len(self._sent) or (self.text and not recalled):
            return False
        self._at = at
        self._put(self._sent[at] if at < len(self._sent) else "")
        return True

    def _put(self, text: str) -> None:
        self.text = text
        self.move_cursor(self.document.end)

    @on(TextArea.Changed)
    def _typed(self) -> None:
        self._menu.offer(self.text)

    async def _on_key(self, event: events.Key) -> None:
        key, chosen = event.key, self._menu.chosen
        if chosen is not None and key in _HISTORY_KEYS:
            self._menu.move(_HISTORY_KEYS[key])
        elif chosen is not None and key == "tab":
            self._put(f"/{chosen} ")
        elif chosen is not None and key == "escape":
            self._menu.offer("")
        elif key == "enter":
            self.post_message(
                self.Submitted(self.text if chosen is None else f"/{chosen}")
            )
        elif key in _NEWLINE_KEYS:
            self.insert("\n")
        elif not (key in _HISTORY_KEYS and self._recall(_HISTORY_KEYS[key])):
            return
        event.stop()
        event.prevent_default()


def _literal(value: object) -> str:
    return pretty_repr(value) if isinstance(value, (dict, list, tuple)) else str(value)


def _one_line(text: Text) -> Text:
    text.no_wrap = True
    text.overflow = "ellipsis"
    return text


class ToolCall(Static, can_focus=True):
    """One call: a line while it runs, a one-line summary once it lands.
    The arguments and the result are drawn whole only when expanded, so a
    large result costs nothing until it is asked for."""

    FOCUS_ON_CLICK = False
    DEFAULT_CSS = """
    ToolCall { border-left: blank $primary; }
    ToolCall:focus { border-left: thick $primary; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space", "fold", show=False),
    ]

    def __init__(self, name: str, arguments: Mapping[str, Any]) -> None:
        super().__init__()
        self._tool = name
        self._arguments = dict(arguments)
        self._outcome: tuple[bool, object] | None = None
        self._elapsed_ms: int | None = None
        self.expanded = False

    def on_mount(self) -> None:
        self._draw()

    def on_click(self) -> None:
        self.action_fold()

    def action_fold(self) -> None:
        self.fold(expanded=not self.expanded)

    def finish(self, success: bool, value: object) -> None:
        self._outcome = (success, value)
        self._draw()

    def progress(self, elapsed_ms: int) -> None:
        self._elapsed_ms = elapsed_ms
        self._draw()

    def fold(self, *, expanded: bool) -> None:
        self.expanded = expanded
        self._draw()

    def _draw(self) -> None:
        if self.is_attached:  # a replayed call takes its result before it mounts
            self.update(self._whole() if self.expanded else self._folded())

    def _head(self, arguments: str) -> Text:
        head = Text("→ ", style="dim")
        head.append(self._tool, style=BRAND_ACCENT)
        head.append(arguments, style="dim")
        return head

    def _call(self) -> str:
        """An argv reads as the command line it is; the rest as a call."""
        argv = self._arguments.get("args")
        if len(self._arguments) == 1 and isinstance(argv, list):
            return " " + shlex.join(map(str, argv))
        return f"({format_args(self._arguments)})"

    def _folded(self) -> RenderableType:
        head = self._head(self._call())
        tail = Text("  ", style="dim")
        if self._outcome is None:
            tail.append("… running")
            if self._elapsed_ms is not None:
                tail.append(f" {self._elapsed_ms} ms")
        else:
            success, value = self._outcome
            lines = _literal(value).strip().splitlines() or [""]
            tail.append("← ")
            tail.append(lines[0], style="default" if success else "red")
            if len(lines) > 1:
                tail.append(f"  (+{len(lines) - 1} lines)")
        return Group(_one_line(head), _one_line(tail))

    def _whole(self) -> RenderableType:
        parts: list[RenderableType] = [self._head(""), Text(_literal(self._arguments))]
        if self._outcome is not None:
            success, value = self._outcome
            lines = _literal(value).splitlines()
            body = Text("\n".join(lines[:_BODY_LINES]), style="" if success else "red")
            if len(lines) > _BODY_LINES:
                body.append(
                    f"\n… {len(lines) - _BODY_LINES} more lines; the turn holds "
                    "it whole (`neosian search`, `recall_turn`)",
                    style="dim",
                )
            parts += [Text("←", style="dim"), body]
        return Group(*parts)


class Working(Static):
    """The line above the prompt while a turn runs."""

    def __init__(self, hint: str, *, id: str) -> None:
        super().__init__(id=id)
        self._hint = hint
        self._started = 0.0
        self._timer: Timer | None = None

    def start(self) -> None:
        self._started = time.perf_counter()
        self.display = True
        self._tick()
        self._timer = self.set_interval(_FRAME_SECONDS, self._tick)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.display = False

    def _tick(self) -> None:
        elapsed = time.perf_counter() - self._started
        frame = _FRAMES[int(elapsed / _FRAME_SECONDS) % len(_FRAMES)]
        line = Text(f"{frame} ", style=BRAND_ACCENT)
        line.append(f"{elapsed:.0f}s  {self._hint}", style="dim")
        self.update(line)
