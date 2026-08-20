"""Shared Rich helpers for the CLI: header art and small formatters."""

import importlib.resources

from rich.console import Console

from neosian._foundation.shared.constants import Assets, PlaygroundUI


def load_header() -> str:
    """Construct the ASCII art header from logo and ascii files."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)

        # Load both files
        logo_text = files.joinpath(Assets.LOGO_FILE).read_text(encoding="utf-8")
        ascii_text = files.joinpath(Assets.ASCII_FILE).read_text(encoding="utf-8")

        # Split into lines
        logo_lines = logo_text.rstrip().split("\n")
        ascii_lines = ascii_text.rstrip().split("\n")

        # Pad to same height
        max_lines = max(len(logo_lines), len(ascii_lines))
        logo_width = max(len(line) for line in logo_lines) if logo_lines else 0

        while len(logo_lines) < max_lines:
            logo_lines.append("")
        while len(ascii_lines) < max_lines:
            ascii_lines.append("")

        # Combine side by side
        combined = []
        for logo_line, ascii_line in zip(logo_lines, ascii_lines, strict=True):
            padded_logo = logo_line.ljust(logo_width)
            combined.append(f"{padded_logo}{Assets.HEADER_SPACING}{ascii_line}")

        return "\n".join(combined)
    except Exception:
        return ""


def print_header(console: Console, agent_name: str) -> None:
    """Print the playground header with ASCII art."""
    ascii_header = load_header()
    if ascii_header:
        console.print(ascii_header, style="cyan")
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
