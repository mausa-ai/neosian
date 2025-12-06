"""neosian CLI entry point.

Provides the main CLI application with commands.
"""

import importlib.resources
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from neosian import __version__
from neosian._cli.playground import run_playground
from neosian._foundation.shared.constants import App, Assets

app = typer.Typer(
    name="neosian",
    help="Stateless agentic AI library. LLM orchestration, tool execution, streaming.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_logo() -> str:
    """Load the small ASCII logo from assets."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)
        logo_file = files.joinpath(Assets.LOGO_FILE)
        return logo_file.read_text(encoding="utf-8").rstrip()
    except Exception:
        return ""


@app.command()
def playground(
    agent_file: Annotated[
        str,
        typer.Argument(help="Path to the agent Python file"),
    ],
    model: Annotated[
        str | None,
        typer.Option(
            "--model", "-m", help="Model to use (default: openai/gpt-oss-20b)"
        ),
    ] = None,
) -> None:
    """Start an interactive playground session with an agent.

    The agent file must define:
        - system_prompt: str
        - tools: list[ToolFunction]

    Example:
        neosian playground my_agent.py
        neosian playground my_agent.py --model llama-3.1-8b-instant
    """
    run_playground(agent_file, model)


@app.command()
def version() -> None:
    """Display version information."""
    console = Console()

    # Load logo
    logo_text = _load_logo()

    # Build version info
    version_info = (
        f"\n"
        f"\n"
        f"  [bold cyan]{App.NAME}[/bold cyan]\n"
        f"  [dim]v{__version__}[/dim]\n"
        f"\n"
        f"  [dim]Python {App.PYTHON_VERSION}[/dim]\n"
        f"  [dim]{App.DESCRIPTION}[/dim]\n"
        f"\n"
    )

    # Create table with logo on left, info on right
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan", vertical="middle")
    table.add_column(vertical="middle")

    table.add_row(logo_text, version_info)

    console.print()
    console.print(table)


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
