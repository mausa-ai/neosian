"""The three things a session asks the human: a yes, a choice, a secret.
Each is a modal the turn (or a command) waits on."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Final

from rich.console import Group
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from neosian._cli.ui import BRAND_ACCENT


class Confirm(ModalScreen[bool]):
    """A command that changes state waits here for the human. Only `y`
    says yes: an enter typed ahead for the prompt must never approve."""

    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Static {
        width: auto; max-width: 90%; height: auto;
        border: round $primary; padding: 1 2;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "answer(True)", show=False),
        Binding("n,escape,ctrl+c", "answer(False)", show=False),
    ]

    def __init__(self, command: str) -> None:
        super().__init__()
        self._command = command

    def compose(self) -> ComposeResult:
        yield Static(
            Group(
                Text("The agent wants to run a command that changes state:"),
                Text(),
                Text(f"  {self._command}", style=f"bold {BRAND_ACCENT}"),
                Text(),
                Text("y runs it   n declines", style="dim"),
            )
        )

    def action_answer(self, approved: bool) -> None:
        self.dismiss(approved)


_BOX: Final = """
    {name} {{ align: center middle; }}
    {name} > Vertical {{
        width: auto; max-width: 90%; height: auto; max-height: 80%;
        border: round $primary; padding: 1 2;
    }}
"""


class Pick(ModalScreen[int | None]):
    """One of a few options, by its index; esc backs out with None."""

    DEFAULT_CSS = _BOX.format(name="Pick") + """
    Pick OptionList, Pick OptionList:focus {
        height: auto; max-height: 16; border: none; margin: 1 0;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,ctrl+c", "back", show=False)
    ]

    def __init__(self, title: str, options: Sequence[str]) -> None:
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self._title, style="bold"))
            yield OptionList(*(Option(Text(option)) for option in self._options))
            yield Static(Text("enter picks   esc backs out", style="dim"))

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_back(self) -> None:
        self.dismiss(None)


class Secret(ModalScreen[str | None]):
    """A value typed masked; esc, or nothing typed, is None."""

    DEFAULT_CSS = _BOX.format(name="Secret") + """
    Secret Input, Secret Input:focus {
        width: 60; margin: 1 0; border: round $primary;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,ctrl+c", "back", show=False)
    ]

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self._title, style="bold"))
            yield Input(password=True)
            yield Static(Text("enter saves   esc backs out", style="dim"))

    @on(Input.Submitted)
    def _entered(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_back(self) -> None:
        self.dismiss(None)
