"""Playground command for interactive agent testing.

Provides a Rich-based chat interface for testing agent definitions.
"""

import asyncio
import importlib.resources
import os
import time
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from simple_term_menu import TerminalMenu  # type: ignore[import-untyped]

from neosian._cli.config import get_api_key
from neosian._cli.loader import load_agent_config
from neosian._cli.session import Session
from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.shared.constants import Assets, Config, PlaygroundUI
from neosian._foundation.shared.types import ModelId, ProviderId


def _load_credentials_from_config() -> None:
    """Load API keys from config file into environment if not already set."""
    # Groq
    if not os.environ.get("GROQ_API_KEY"):
        groq_key = get_api_key(Config.GROQ_API_KEY)
        if groq_key:
            os.environ["GROQ_API_KEY"] = groq_key

    # OpenAI
    if not os.environ.get("OPENAI_API_KEY"):
        openai_key = get_api_key(Config.OPENAI_API_KEY)
        if openai_key:
            os.environ["OPENAI_API_KEY"] = openai_key


def _load_header() -> str:
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


def _get_models_for_provider(provider_id: str) -> list[tuple[str, str]]:
    """Get available models for a provider.

    Returns:
        List of (model_id, display_name) tuples.
    """
    from neosian._foundation.shared.constants import Provider

    match provider_id:
        case Provider.Groq.ID:
            return [
                (Provider.Groq.Production.GPT_OSS_20B, "openai/gpt-oss-20b (default)"),
                (Provider.Groq.Production.GPT_OSS_120B, "openai/gpt-oss-120b"),
                (Provider.Groq.Production.LLAMA_3_3_70B, "llama-3.3-70b-versatile"),
                (Provider.Groq.Production.LLAMA_3_1_8B, "llama-3.1-8b-instant"),
                (
                    Provider.Groq.Preview.LLAMA_4_MAVERICK_17B,
                    "llama-4-maverick-17b (preview)",
                ),
                (
                    Provider.Groq.Preview.LLAMA_4_SCOUT_17B,
                    "llama-4-scout-17b (preview)",
                ),
                (Provider.Groq.Preview.QWEN3_32B, "qwen3-32b (preview)"),
                (Provider.Groq.Preview.KIMI_K2, "kimi-k2 (preview)"),
            ]
        case Provider.OpenAI.ID:
            return [
                (Provider.OpenAI.Models.GPT_5_NANO, "gpt-5-nano (default, fastest)"),
                (Provider.OpenAI.Models.GPT_5_MINI, "gpt-5-mini (balanced)"),
                (Provider.OpenAI.Models.GPT_5_1, "gpt-5.1 (best for coding)"),
                (Provider.OpenAI.Models.GPT_5_PRO, "gpt-5-pro (most precise)"),
            ]
        case _:
            return []


def _select_provider_and_model(console: Console) -> tuple[str, str] | None:
    """Show interactive menu to select provider and model.

    Returns:
        Tuple of (provider_id, model_id) or None if cancelled.
    """
    from neosian._foundation.shared.constants import Provider

    # Provider selection
    providers = [
        (Provider.Groq.ID, "Groq (fastest inference)"),
        (Provider.OpenAI.ID, "OpenAI"),
    ]

    console.print("\n[bold]Select Provider:[/bold]")
    provider_menu = TerminalMenu(
        [p[1] for p in providers],
        cursor_index=0,
    )
    provider_choice = provider_menu.show()

    if provider_choice is None:
        return None

    selected_provider = providers[provider_choice][0]

    # Model selection based on provider
    models = _get_models_for_provider(selected_provider)
    if not models:
        return None

    console.print(f"\n[bold]Select Model ({selected_provider}):[/bold]")
    model_menu = TerminalMenu(
        [m[1] for m in models],
        cursor_index=0,
    )
    model_choice = model_menu.show()

    if model_choice is None:
        return None

    selected_model = models[model_choice][0]
    return (selected_provider, selected_model)


def run_playground(agent_path: str, menu: bool = False) -> None:
    """Run the playground with the given agent file.

    Args:
        agent_path: Path to the agent Python file.
        menu: Show interactive menu to select provider and model.
    """
    console = Console()

    # Load credentials from config file if not in environment
    _load_credentials_from_config()

    # Load agent configuration
    try:
        config, agent_name = load_agent_config(agent_path)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e

    # Interactive menu override
    if menu:
        selection = _select_provider_and_model(console)
        if selection is None:
            console.print("[dim]Cancelled.[/dim]")
            return

        selected_provider, selected_model = selection

        from neosian._foundation.shared.types import AgentConfig

        config = AgentConfig(
            system_prompt=config.system_prompt,
            tools=config.tools,
            provider=ProviderId(selected_provider),
            model=ModelId(selected_model),
            enable_todo=config.enable_todo,
        )

    # Create agent from config
    try:
        agent = Agent(config=config)
    except Exception as e:
        console.print(f"[red]Error creating agent: {e}[/red]")
        raise SystemExit(1) from e

    # Determine provider and model for display
    display_provider = str(config.provider)
    display_model = str(agent._model)

    # Create session
    session = Session(agent_name=agent_name)

    # Print header
    _print_header(console, agent_name)

    # Run chat loop
    try:
        asyncio.run(
            _chat_loop(console, agent, session, display_provider, display_model)
        )
    except KeyboardInterrupt:
        console.print()  # New line after ^C

    # Handle exit
    _handle_exit(console, session)


def _print_header(console: Console, agent_name: str) -> None:
    """Print the playground header with ASCII art."""
    # Print ASCII art header
    ascii_header = _load_header()
    if ascii_header:
        console.print(ascii_header, style="cyan")
        console.print()

    # Print agent info
    console.print(PlaygroundUI.AGENT_LOADED.format(name=agent_name), style="bold")
    console.print(f"[dim]{PlaygroundUI.SESSION_START}[/dim]\n")


async def _chat_loop(
    console: Console,
    agent: Agent,
    session: Session,
    provider: str,
    model: str,
) -> None:
    """Run the main chat loop."""
    while True:
        # Get user input
        try:
            user_input = Prompt.ask(
                f"[bold green]{PlaygroundUI.USER_PROMPT}[/bold green]"
            )
        except EOFError:
            break

        # Check for exit commands
        if user_input.lower() in PlaygroundUI.EXIT_COMMANDS:
            break

        if not user_input.strip():
            continue

        # Add user message to session
        session.add_user_message(user_input)

        # Start timing
        start_time = time.perf_counter()

        # Show thinking indicator
        with console.status(f"[dim]{PlaygroundUI.THINKING}[/dim]"):
            # Build message history for agent
            messages = [
                Message(
                    role=Role.USER if m.role == Role.USER else Role.ASSISTANT,
                    content=m.content or "",
                )
                for m in session.get_messages()
            ]

            # Get response
            try:
                response = await agent.run(messages, stream=False)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                continue

        # Calculate elapsed time
        elapsed_time = time.perf_counter() - start_time

        # Display tool calls if any
        for i, tool_call in enumerate(response.tool_calls_made):
            tool_text = Text()
            tool_text.append(f"{tool_call.name}", style="yellow")
            tool_text.append(f"({_format_args(tool_call.arguments)})", style="dim")

            if i < len(response.tool_results):
                result = response.tool_results[i]
                if result.success:
                    tool_text.append(" → ", style="dim")
                    tool_text.append(str(result.data)[:100], style="green")
                else:
                    tool_text.append(" → ", style="dim")
                    tool_text.append(str(result.error), style="red")

            console.print(
                Panel(
                    tool_text, title=PlaygroundUI.TOOL_CALL_LABEL, border_style="yellow"
                )
            )

        # Display assistant response
        if response.message.content:
            session.add_assistant_message(response.message.content)
            title = Text()
            title.append(provider, style="cyan")
            title.append("/", style="dim")
            title.append(model, style="blue")
            console.print(
                Panel(
                    Markdown(response.message.content),
                    title=title,
                    border_style="blue",
                )
            )

        # Display response time
        console.print(_format_elapsed_time(elapsed_time), style="dim")
        console.print()


def _format_args(args: dict[str, object]) -> str:
    """Format tool arguments for display."""
    parts = []
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 30:
            value = value[:30] + "..."
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _format_elapsed_time(seconds: float) -> str:
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


def _handle_exit(console: Console, session: Session) -> None:
    """Handle exit: prompt to save session with arrow menu."""
    console.print()

    if not session.messages:
        console.print(f"[dim]{PlaygroundUI.GOODBYE}[/dim]")
        return

    # Show save menu with arrow selection
    console.print(f"[bold]{PlaygroundUI.SAVE_MENU_TITLE}[/bold]")

    options = [PlaygroundUI.SAVE_OPTION_YES, PlaygroundUI.SAVE_OPTION_NO]
    menu = TerminalMenu(
        options,
        cursor_index=1,  # Default to "No"
    )
    choice = menu.show()

    # choice is 0 for Yes, 1 for No, None if cancelled
    if choice == 0:
        # Save to current directory
        filename = session.generate_filename()
        path = session.save(Path.cwd() / filename)
        console.print(
            PlaygroundUI.SESSION_SAVED.format(path=path),
            style="green",
        )
    else:
        console.print(f"[dim]{PlaygroundUI.SESSION_DISCARDED}[/dim]")

    console.print(f"[dim]{PlaygroundUI.GOODBYE}[/dim]")
