"""Shared Rich helpers for the CLI: the mark and small formatters."""

import importlib.resources
import textwrap
from typing import TextIO

from rich.console import Console
from rich.text import Text

from neosian._foundation.shared.constants import Assets

# The brand colours (branding/README.md), at the dark-theme lightness the
# kit's clamp derives — terminals are dark far more often than not, and Rich
# downgrades both to the nearest of 256 colours where truecolor is missing.
BRAND_ACCENT = "#afaf73"  # maki: the wordmark art, the app name
BRAND_SUPPORT = "#c6a850"  # maki sarısı: the mark


def load_mark() -> Text:
    """The mark in the support colour, flush left; empty when the asset
    cannot be read."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)
        art = files.joinpath(Assets.LOGO_FILE).read_text(encoding="utf-8")
    except Exception:
        return Text()
    return Text(textwrap.dedent(art).strip("\n"), style=BRAND_SUPPORT)


def pick(
    console: Console, title: str, options: list[str], *, default: int = 0
) -> int | None:
    """A numbered menu on `rich.prompt` (DESIGN §30: nothing in the shell
    is platform-bound): the index chosen, None on EOF."""
    from rich.prompt import Prompt

    console.print()
    console.print(Text(title, style=f"bold {BRAND_ACCENT}"))
    for number, option in enumerate(options, start=1):
        console.print(Text(f"  {number}. {option}"))
    choices = [str(n) for n in range(1, len(options) + 1)]
    try:
        answer = Prompt.ask(
            "choice", choices=choices, default=str(default + 1), console=console
        )
    except EOFError:
        return None
    return int(answer) - 1


def checklist(console: Console, options: list[str], stdin: TextIO) -> list[int] | None:
    """All selected initially; numbers toggle, Enter confirms, EOF cancels."""
    selected = set(range(len(options)))
    console.print(Text("Connect clients", style=f"bold {BRAND_ACCENT}"))
    while True:
        for index, option in enumerate(options):
            mark = "x" if index in selected else " "
            console.print(Text(f"  {index + 1}. [{mark}] {option}"))
        console.print("Numbers toggle; Enter confirms; q cancels: ", end="")
        try:
            line = stdin.readline()
        except KeyboardInterrupt:
            return None
        if not line or line.strip().lower() == "q":
            return None
        if not line.strip():
            return sorted(selected)
        try:
            toggles = {int(number) - 1 for number in line.replace(",", " ").split()}
            if not toggles <= set(range(len(options))):
                raise ValueError
        except ValueError:
            console.print(Text("Enter numbers from the list.", style="red"))
            continue
        selected.symmetric_difference_update(toggles)


def format_args(args: dict[str, object]) -> str:
    """Format tool arguments for display."""
    parts = []
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 30:
            value = value[:30] + "..."
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed time for display.

    Args:
        seconds: Elapsed time in seconds.

    Returns:
        Formatted string like "1.23s" or "1m 23s".
    """
    if seconds < 60:
        return f"Response time: {seconds:.2f}s"
    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60
    return f"Response time: {minutes}m {remaining_seconds:.2f}s"


def turn_footer(title: Text, seconds: float, cost: int | None) -> Text:
    """The same model, timing and integer-micro-dollar receipt in both paths."""
    from neosian._foundation.shared.types import format_micro_usd

    footer = title.copy()
    footer.append(f"  {format_elapsed_time(seconds)}", style="dim")
    if cost is not None:
        footer.append(f"  {format_micro_usd(cost)}", style="dim")
    return footer
