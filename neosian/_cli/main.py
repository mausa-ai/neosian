"""neosian CLI entry point.

Provides the main CLI application with commands.
"""

import asyncio
import importlib.resources
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from simple_term_menu import TerminalMenu  # type: ignore[import-untyped]

from neosian import __version__
from neosian._cli.config import (
    delete_config,
    get_all_credentials,
    get_config_path,
    set_api_key,
)
from neosian._cli.playground import run_playground
from neosian._foundation.shared.constants import App, Assets, Config

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
    menu: Annotated[
        bool,
        typer.Option(
            "--menu", help="Show interactive menu to select provider and model"
        ),
    ] = False,
    arena: Annotated[
        bool,
        typer.Option(
            "--arena", help="Run in arena mode with multiple models side-by-side"
        ),
    ] = False,
) -> None:
    """Start an interactive playground session with an agent.

    The agent file must define:
        - system_prompt: str
        - tools: list[ToolFunction]

    Example:
        neosian playground my_agent.py
        neosian playground my_agent.py --menu
        neosian playground my_agent.py --arena
    """
    run_playground(agent_file, menu=menu, arena=arena)


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


def _mask_key(key: str) -> str:
    """Mask an API key for display.

    Shows first 4 and last 3 characters.

    Args:
        key: The API key to mask.

    Returns:
        Masked key like "gsk_****...****xyz".
    """
    if len(key) <= 7:
        return "****"
    return f"{key[:4]}****...****{key[-3:]}"


def _show_credentials_table(console: Console) -> None:
    """Display credentials status table."""
    credentials = get_all_credentials()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Key")

    # Groq
    groq_key = credentials.get(Config.GROQ_API_KEY)
    if groq_key:
        table.add_row("Groq", "[green]●[/green]", _mask_key(groq_key))
    else:
        table.add_row("Groq", "[red]●[/red]", "[dim]Not configured[/dim]")

    # OpenAI
    openai_key = credentials.get(Config.OPENAI_API_KEY)
    if openai_key:
        table.add_row("OpenAI", "[green]●[/green]", _mask_key(openai_key))
    else:
        table.add_row("OpenAI", "[red]●[/red]", "[dim]Not configured[/dim]")

    # Anthropic
    anthropic_key = credentials.get(Config.ANTHROPIC_API_KEY)
    if anthropic_key:
        table.add_row("Anthropic", "[green]●[/green]", _mask_key(anthropic_key))
    else:
        table.add_row("Anthropic", "[red]●[/red]", "[dim]Not configured[/dim]")

    # Cerebras
    cerebras_key = credentials.get(Config.CEREBRAS_API_KEY)
    if cerebras_key:
        table.add_row("Cerebras", "[green]●[/green]", _mask_key(cerebras_key))
    else:
        table.add_row("Cerebras", "[red]●[/red]", "[dim]Not configured[/dim]")

    console.print()
    console.print(table)
    console.print(f"\n[dim]Config file: {get_config_path()}[/dim]")


def _configure_credentials(console: Console) -> None:
    """Prompt for and save credentials."""
    credentials = get_all_credentials()

    console.print("\n[dim]Press Enter to keep existing values.[/dim]\n")

    # Groq
    existing_groq = credentials.get(Config.GROQ_API_KEY, "")
    groq_prompt = "Groq API key"
    if existing_groq:
        groq_prompt += f" [dim]({_mask_key(existing_groq)})[/dim]"

    groq_key = Prompt.ask(groq_prompt, password=True, default="")
    if groq_key:
        set_api_key(Config.GROQ_API_KEY, groq_key)
        console.print("[green]Groq API key saved.[/green]")
    elif existing_groq:
        console.print("[dim]Groq API key unchanged.[/dim]")

    # OpenAI
    existing_openai = credentials.get(Config.OPENAI_API_KEY, "")
    openai_prompt = "OpenAI API key"
    if existing_openai:
        openai_prompt += f" [dim]({_mask_key(existing_openai)})[/dim]"

    openai_key = Prompt.ask(openai_prompt, password=True, default="")
    if openai_key:
        set_api_key(Config.OPENAI_API_KEY, openai_key)
        console.print("[green]OpenAI API key saved.[/green]")
    elif existing_openai:
        console.print("[dim]OpenAI API key unchanged.[/dim]")

    # Anthropic
    existing_anthropic = credentials.get(Config.ANTHROPIC_API_KEY, "")
    anthropic_prompt = "Anthropic API key"
    if existing_anthropic:
        anthropic_prompt += f" [dim]({_mask_key(existing_anthropic)})[/dim]"

    anthropic_key = Prompt.ask(anthropic_prompt, password=True, default="")
    if anthropic_key:
        set_api_key(Config.ANTHROPIC_API_KEY, anthropic_key)
        console.print("[green]Anthropic API key saved.[/green]")
    elif existing_anthropic:
        console.print("[dim]Anthropic API key unchanged.[/dim]")

    # Cerebras
    existing_cerebras = credentials.get(Config.CEREBRAS_API_KEY, "")
    cerebras_prompt = "Cerebras API key"
    if existing_cerebras:
        cerebras_prompt += f" [dim]({_mask_key(existing_cerebras)})[/dim]"

    cerebras_key = Prompt.ask(cerebras_prompt, password=True, default="")
    if cerebras_key:
        set_api_key(Config.CEREBRAS_API_KEY, cerebras_key)
        console.print("[green]Cerebras API key saved.[/green]")
    elif existing_cerebras:
        console.print("[dim]Cerebras API key unchanged.[/dim]")


def _delete_configuration(console: Console) -> None:
    """Delete configuration with confirmation."""
    confirm = Prompt.ask(
        "\n[yellow]Delete all stored credentials?[/yellow] [dim](y/n)[/dim]",
        default="n",
    )
    if confirm.lower() == "y":
        if delete_config():
            console.print("[green]Configuration deleted.[/green]")
        else:
            console.print("[dim]No configuration to delete.[/dim]")
    else:
        console.print("[dim]Cancelled.[/dim]")


@app.command()
def configure() -> None:
    """Configure API credentials for LLM providers.

    Shows current configuration status and provides options to
    configure or delete stored credentials.

    Credentials are stored in ~/.neosian/config.toml.
    Environment variables take precedence over stored credentials.

    Example:
        neosian configure
    """
    console = Console()

    # Show current status
    _show_credentials_table(console)

    # Show menu
    console.print()
    options = ["Configure credentials", "Delete configuration", "Exit"]
    menu = TerminalMenu(options, cursor_index=0)
    choice = menu.show()

    if choice == 0:
        _configure_credentials(console)
    elif choice == 1:
        _delete_configuration(console)
    # choice == 2 or None (cancelled) -> just exit


@app.command(name="eval")
def evaluate(
    config_file: Annotated[
        str,
        typer.Argument(help="Path to the evaluation config YAML file"),
    ],
) -> None:
    """Run agent evaluation against prompt × model matrix.

    Tests tool selection and parameter passing across different
    prompt files and models. Results are displayed in terminal
    and saved to JSON.

    Example:
        neosian eval eval_config.yaml
    """
    from neosian._cli.playground import _load_credentials_from_config
    from neosian._foundation.evaluation import (
        EvalProgress,
        create_progress_callback,
        load_eval_config,
        print_results,
        run_evaluation,
        save_results,
    )

    # The library reads keys from the environment only; loading them from the
    # CLI config file is the CLI's job, done here before the run.
    _load_credentials_from_config()

    console = Console()

    # Load config
    try:
        config = load_eval_config(config_file)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from None

    console.print()

    # Create progress display
    progress = EvalProgress(config)

    # Run evaluation with live progress
    try:
        progress.start()
        results = asyncio.run(
            run_evaluation(config, on_progress=create_progress_callback(progress))
        )
        progress.stop()
    except Exception as e:
        progress.stop()
        console.print(f"[red]Evaluation failed: {e}[/red]")
        raise typer.Exit(1) from None

    console.print()

    # Print results
    print_results(config, results, console)

    # Save results
    output_path = save_results(config, results)
    console.print()
    console.print(f"[dim]{output_path}[/dim]")


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
