"""Shared Rich helpers for the CLI: header art and small formatters."""

import importlib.resources

from rich.console import Console
from rich.text import Text

from neosian._foundation.shared.constants import Assets, PlaygroundUI

# The brand colours (branding/README.md), at the dark-theme lightness the
# kit's clamp derives — terminals are dark far more often than not, and Rich
# downgrades both to the nearest of 256 colours where truecolor is missing.
BRAND_ACCENT = "#afaf73"  # maki: the wordmark art, the app name
BRAND_SUPPORT = "#c6a850"  # maki sarısı: the mark
_LOGO_DROP = 1  # rows the mark sits below the wordmark's i-dot, so their bases align


def load_header() -> Text:
    """Build the header: the mark in the support colour, the wordmark in the accent."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)

        # Load both files
        logo_text = files.joinpath(Assets.LOGO_FILE).read_text(encoding="utf-8")
        ascii_text = files.joinpath(Assets.ASCII_FILE).read_text(encoding="utf-8")

        # Split into lines
        logo_lines = [""] * _LOGO_DROP + logo_text.rstrip().split("\n")
        ascii_lines = ascii_text.rstrip().split("\n")

        # Pad to same height
        max_lines = max(len(logo_lines), len(ascii_lines))
        logo_width = max(len(line) for line in logo_lines) if logo_lines else 0

        while len(logo_lines) < max_lines:
            logo_lines.append("")
        while len(ascii_lines) < max_lines:
            ascii_lines.append("")

        # Combine side by side, each column in its own colour
        header = Text()
        for logo_line, ascii_line in zip(logo_lines, ascii_lines, strict=True):
            header.append(logo_line.ljust(logo_width), style=BRAND_SUPPORT)
            header.append(f"{Assets.HEADER_SPACING}{ascii_line}\n", style=BRAND_ACCENT)
        header.rstrip()
        return header
    except Exception:
        return Text()


def print_header(console: Console, agent_name: str) -> None:
    """Print the playground header with ASCII art."""
    header = load_header()
    if header.plain:
        console.print(header)
        console.print()

    console.print(PlaygroundUI.AGENT_LOADED.format(name=agent_name), style="bold")
    console.print(f"[dim]{PlaygroundUI.SESSION_START}[/dim]\n")


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
