"""The `neosian version` banner: the mark, the version, the doors the
install carries (NX, ledger #206)."""

from __future__ import annotations

import importlib.resources
from importlib.util import find_spec

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from neosian import __version__
from neosian._cli.ui import BRAND_ACCENT
from neosian._foundation.shared.constants import App, Assets


def _load_logo() -> str:
    """Load the small ASCII logo from assets."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)
        logo_file = files.joinpath(Assets.LOGO_FILE)
        return logo_file.read_text(encoding="utf-8").rstrip()
    except Exception:
        return ""


def _driver_line() -> str:
    """The one extra, by presence: the banner says what this install carries."""
    if find_spec("psycopg") is None:
        return 'psycopg (PostgresStore): missing\n  uv add "neosian[postgres]" adds it'
    return "psycopg (PostgresStore): installed"


def print_banner() -> None:
    console = Console()

    # Load logo
    logo_text = _load_logo()

    # Build version info
    version_info = (
        f"\n"
        f"\n"
        f"  [bold {BRAND_ACCENT}]{App.NAME}[/bold {BRAND_ACCENT}]\n"
        f"  [dim]v{__version__}[/dim]\n"
        f"\n"
        f"  [dim]Python {App.PYTHON_VERSION}[/dim]\n"
        f"  [dim]{App.DESCRIPTION}[/dim]\n"
        f"\n"
        f"  [dim]in the box: Agent + Conversation,[/dim]\n"
        f"  [dim]the shell, MCP server and client,[/dim]\n"
        f"  [dim]the state process, OpenTelemetry;[/dim]\n"
        f"  [dim]{escape(_driver_line())}[/dim]\n"
        f"\n"
    )

    # Create table with logo on left, info on right
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan", vertical="middle")
    table.add_column(vertical="middle")

    table.add_row(logo_text, version_info)

    console.print()
    console.print(table)
