"""Playground command for interactive agent testing.

Provides a Rich-based chat interface for testing agent definitions.
"""

import asyncio
import importlib.resources
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from simple_term_menu import TerminalMenu  # type: ignore[import-untyped]

from neosian._cli.loader import load_agent_definition
from neosian._cli.session import Session
from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.shared.constants import (
    Assets,
    ErrorMessages,
    PlaygroundUI,
    Provider,
)
from neosian._foundation.shared.exceptions import MissingAPIKeyError
from neosian._foundation.shared.types import ModelId


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


def run_playground(agent_path: str, model: str | None = None) -> None:
    """Run the playground with the given agent file.

    Args:
        agent_path: Path to the agent Python file.
        model: Optional model override.
    """
    console = Console()

    # Load agent definition
    try:
        definition = load_agent_definition(agent_path)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e

    # Get API key
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise MissingAPIKeyError(ErrorMessages.GROQ_API_KEY_MISSING)

    # Create client and agent
    client = GroqClient(api_key=api_key)
    model_id = ModelId(model or Provider.Groq.DEFAULT_MODEL)

    agent = Agent(
        client=client,
        model=model_id,
        system_prompt=definition.system_prompt,
        tools=definition.tools,
    )

    # Create session
    session = Session(agent_name=definition.name)

    # Print header
    _print_header(console, definition.name, model_id)

    # Run chat loop
    try:
        asyncio.run(_chat_loop(console, agent, session))
    except KeyboardInterrupt:
        console.print()  # New line after ^C

    # Handle exit
    _handle_exit(console, session)


def _print_header(console: Console, agent_name: str, model: ModelId) -> None:
    """Print the playground header with ASCII art."""
    # Print ASCII art header
    ascii_header = _load_header()
    if ascii_header:
        console.print(ascii_header, style="cyan")
        console.print()

    # Print agent info
    info = Text()
    info.append(PlaygroundUI.AGENT_LOADED.format(name=agent_name), style="bold")
    info.append(f" | Model: {model}", style="dim")
    console.print(info)
    console.print(f"[dim]{PlaygroundUI.SESSION_START}[/dim]\n")


async def _chat_loop(console: Console, agent: Agent, session: Session) -> None:
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
                response = await agent.run(messages)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                continue

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
            console.print(
                Panel(
                    response.message.content,
                    title=PlaygroundUI.ASSISTANT_LABEL,
                    border_style="blue",
                )
            )
        console.print()


def _format_args(args: dict[str, object]) -> str:
    """Format tool arguments for display."""
    parts = []
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 30:
            value = value[:30] + "..."
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


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
