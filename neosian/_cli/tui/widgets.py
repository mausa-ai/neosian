"""The session view's pieces: the prompt, a tool call that folds, the
working line. Application data is literal `Text`, never markup."""

from __future__ import annotations

import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from rich.console import Group, RenderableType
from rich.pretty import pretty_repr
from rich.text import Text
from textual import events
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


class Prompt(TextArea):
    """Enter sends; ctrl+j breaks the line (shift+enter too, where the
    terminal reports it); up and down walk what this session sent, from
    an empty prompt or a recalled line, never over a draft."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "app.cancel", show=False),
        Binding("ctrl+d", "app.quit", show=False),
    ]

    @dataclass
    class Submitted(Message):
        text: str

    def __init__(self, *, id: str, placeholder: str) -> None:
        super().__init__(id=id, placeholder=placeholder)
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
        self.text = self._sent[at] if at < len(self._sent) else ""
        self.move_cursor(self.document.end)
        return True

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.post_message(self.Submitted(self.text))
        elif event.key in _NEWLINE_KEYS:
            self.insert("\n")
        elif not (
            event.key in _HISTORY_KEYS and self._recall(_HISTORY_KEYS[event.key])
        ):
            return
        event.stop()
        event.prevent_default()


def _literal(value: object) -> str:
    return pretty_repr(value) if isinstance(value, (dict, list, tuple)) else str(value)


def _one_line(text: Text) -> Text:
    text.no_wrap = True
    text.overflow = "ellipsis"
    return text


class ToolCall(Static):
    """One call: a line while it runs, a one-line summary once it lands.
    The arguments and the result are drawn whole only when expanded, so a
    large result costs nothing until it is asked for."""

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
