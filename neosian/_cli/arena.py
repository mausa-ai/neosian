"""Arena mode: N models side-by-side over one shared message list.

Arena is deliberately memory-less and stays off `Conversation` — N models
writing one store is not a dogfood, it is a race. History lives in memory
for the run; `ArenaSession` offers the opt-in JSON save at exit.
"""

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from simple_term_menu import TerminalMenu  # type: ignore[import-untyped]

from neosian._cli.models import select_arena_models
from neosian._cli.session import ArenaModelResponse, ArenaSession
from neosian._cli.ui import format_args, print_header
from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.constants import ArenaUI, PlaygroundUI
from neosian._foundation.shared.types import AgentConfig, AnyModel


def arena_config(base: AgentConfig, model: AnyModel) -> AgentConfig:
    """Per-model arena config: everything rides `replace()` so no field
    can be silently dropped (the --menu bug's twin); `memory=None` is the
    one deliberate difference — arena stays memory-less."""
    return dataclasses.replace(base, model=model, memory=None)


@dataclass
class ArenaModelResult:
    """Result from a single model in arena mode."""

    provider: str
    model: str
    content: str
    tool_calls_text: list[Text]
    tool_calls_raw: list[dict[str, object]]  # For JSON serialization
    elapsed_time: float
    error: str | None = None
    blocked: bool = False
    blocked_rationale: str | None = None


def _build_arena_cell_content(result: ArenaModelResult) -> RenderableType:
    """Build the content for a single arena table cell (response only, no timing).

    Args:
        result: The model result to display.

    Returns:
        Renderable content for the table cell.
    """
    from rich.console import Group

    parts: list[RenderableType] = []

    # Add tool calls if any
    for tool_text in result.tool_calls_text:
        parts.append(
            Panel(tool_text, title=PlaygroundUI.TOOL_CALL_LABEL, border_style="yellow")
        )

    # Handle blocked, error, or normal response
    if result.blocked:
        # Build blocked message
        blocked_text = Text()
        blocked_text.append(PlaygroundUI.GUARDRAIL_INPUT_BLOCKED, style="bold red")

        if result.blocked_rationale:
            blocked_text.append("\n")
            blocked_text.append(
                PlaygroundUI.GUARDRAIL_RATIONALE.format(
                    rationale=result.blocked_rationale
                ),
                style="dim",
            )

        parts.append(
            Panel(
                blocked_text,
                title=PlaygroundUI.GUARDRAIL_BLOCKED_LABEL,
                border_style="red",
            )
        )
    elif result.error:
        parts.append(Text(f"Error: {result.error}", style="red"))
    elif result.content:
        parts.append(Markdown(result.content))

    return Group(*parts)


async def _run_arena_model_with_session(
    agent_session: AgentSession,
    messages: list[Message],
    provider: str,
    model: str,
) -> ArenaModelResult:
    """Run a single model using cached session and collect results.

    Args:
        agent_session: The agent session with cached HTTP client.
        messages: Message history.
        provider: Provider ID for display.
        model: Model ID for display.

    Returns:
        ArenaModelResult with response data.
    """
    start_time = time.perf_counter()

    try:
        response = await agent_session.run(messages, stream=False)
    except Exception as e:
        return ArenaModelResult(
            provider=provider,
            model=model,
            content="",
            tool_calls_text=[],
            tool_calls_raw=[],
            elapsed_time=time.perf_counter() - start_time,
            error=str(e),
        )

    return _build_arena_result(response, provider, model, start_time)


def _build_arena_result(
    response: object,  # AgentResponse, but avoid circular import
    provider: str,
    model: str,
    start_time: float,
) -> ArenaModelResult:
    """Build ArenaModelResult from response.

    Args:
        response: The agent response.
        provider: Provider ID for display.
        model: Model ID for display.
        start_time: Start time for elapsed calculation.

    Returns:
        ArenaModelResult with response data.
    """
    from neosian._foundation.agent.response import AgentResponse

    # Type assertion for mypy
    assert isinstance(response, AgentResponse)

    elapsed_time = time.perf_counter() - start_time

    # Format tool calls for display and raw serialization
    tool_calls_text: list[Text] = []
    tool_calls_raw: list[dict[str, object]] = []

    for i, tool_call in enumerate(response.tool_calls_made):
        # Raw data for JSON
        tool_calls_raw.append(
            {
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
        )

        # Formatted text for display
        tool_text = Text()
        tool_text.append(f"{tool_call.name}", style="yellow")
        tool_text.append(f"({format_args(tool_call.arguments)})", style="dim")

        if i < len(response.tool_results):
            result = response.tool_results[i]
            if result.success:
                tool_text.append(" → ", style="dim")
                tool_text.append(str(result.data), style="green")
            else:
                tool_text.append(" → ", style="dim")
                tool_text.append(str(result.error), style="red")

        tool_calls_text.append(tool_text)

    # Check if blocked by guardrails
    blocked = response.blocked
    blocked_rationale: str | None = None

    if blocked and response.guardrail_result:
        blocked_rationale = response.guardrail_result.policy_rationale

    return ArenaModelResult(
        provider=provider,
        model=model,
        content=text_of(response.message),
        tool_calls_text=tool_calls_text,
        tool_calls_raw=tool_calls_raw,
        elapsed_time=elapsed_time,
        blocked=blocked,
        blocked_rationale=blocked_rationale,
    )


def _build_arena_vs_header(providers: list[str], models: list[str]) -> Text:
    """Build the VS header for arena mode.

    Args:
        providers: List of provider IDs.
        models: List of model IDs.

    Returns:
        Styled Text with "provider/model vs provider/model vs ..."
    """
    header = Text()
    for i, (provider, model) in enumerate(zip(providers, models, strict=True)):
        if i > 0:
            header.append(" vs ", style="bold white")
        color = ArenaUI.COLORS[i % len(ArenaUI.COLORS)]
        header.append(f"{provider}/{model}", style=f"bold {color}")
    return header


def _display_arena_results(console: Console, results: list[ArenaModelResult]) -> None:
    """Display arena results in a side-by-side table.

    Args:
        console: Rich console for output.
        results: List of model results to display.
    """
    # Print VS header
    providers = [r.provider for r in results]
    models = [r.model for r in results]
    vs_header = _build_arena_vs_header(providers, models)
    console.print(vs_header)
    console.print()

    # Build table with colored columns
    table = Table(show_header=True, header_style="bold", expand=True)

    # Add columns with colored headers matching the VS header
    for i, result in enumerate(results):
        color = ArenaUI.COLORS[i % len(ArenaUI.COLORS)]
        header = Text()
        header.append(result.provider, style=color)
        header.append("/", style="dim")
        header.append(result.model, style=color)
        table.add_column(header, ratio=1)

    # Add row with content
    cells = [_build_arena_cell_content(result) for result in results]
    table.add_row(*cells)

    # Add timing row with matching colors
    timing_cells = [
        Text(
            f"{result.elapsed_time:.2f}s", style=ArenaUI.COLORS[i % len(ArenaUI.COLORS)]
        )
        for i, result in enumerate(results)
    ]
    table.add_row(*timing_cells)

    console.print(table)


async def _arena_chat_loop(
    console: Console,
    agents: list[Agent],
    providers: list[str],
    models: list[str],
    session: ArenaSession,
) -> None:
    """Run the arena chat loop with multiple models.

    Uses AgentSession to cache HTTP clients for each agent across turns.

    Args:
        console: Rich console for output.
        agents: List of agents to run.
        providers: List of provider IDs for display.
        models: List of model IDs for display.
        session: Arena session to collect data.
    """
    messages: list[Message] = []

    # Create sessions for all agents (cache HTTP clients across turns)
    agent_sessions: list[AgentSession] = []
    for agent in agents:
        agent_sessions.append(AgentSession(agent))

    try:
        while True:
            # Get user input
            try:
                user_input = Prompt.ask(
                    f"[bold green]{PlaygroundUI.USER_PROMPT}[/bold green]"
                )
            except EOFError:
                break

            if user_input.lower() in PlaygroundUI.EXIT_COMMANDS:
                break

            if not user_input.strip():
                continue

            # Add user message
            messages.append(Message(role=Role.USER, content=user_input))

            # Create turn for session
            turn = session.add_turn(user_input)

            # Run each model sequentially using cached sessions
            results: list[ArenaModelResult] = []
            for i, agent_session in enumerate(agent_sessions):
                color = ArenaUI.COLORS[i % len(ArenaUI.COLORS)]
                status_text = Text()
                status_text.append("Running ", style="dim")
                status_text.append(providers[i], style=color)
                status_text.append("/", style="dim")
                status_text.append(models[i], style=color)
                status_text.append("...", style="dim")
                with console.status(status_text):
                    result = await _run_arena_model_with_session(
                        agent_session, messages, providers[i], models[i]
                    )
                    results.append(result)

                    # Add to session turn
                    turn.responses.append(
                        ArenaModelResponse(
                            provider=result.provider,
                            model=result.model,
                            content=result.content,
                            tool_calls=result.tool_calls_raw,
                            elapsed_time=result.elapsed_time,
                            error=result.error,
                        )
                    )

            # Display results
            _display_arena_results(console, results)
            console.print()
    finally:
        # Close all agent sessions
        for agent_session in agent_sessions:
            await agent_session.close()


def run_arena_mode(
    console: Console,
    base_config: AgentConfig,
    agent_name: str,
) -> None:
    """Run arena mode with multiple models.

    Args:
        console: Rich console for output.
        base_config: Base agent configuration.
        agent_name: Name of the agent.
    """
    # Select models
    require_reasoning = base_config.reasoning_effort is not None
    selected_models = select_arena_models(console, require_reasoning=require_reasoning)
    if selected_models is None:
        console.print("[dim]Cancelled.[/dim]")
        return

    # Create agents
    agents: list[Agent] = []
    providers: list[str] = []
    models: list[str] = []

    for model in selected_models:
        config = arena_config(base_config, model)
        try:
            agent = Agent(config=config)
            agents.append(agent)
            providers.append(model.provider.value)
            models.append(model.value)
        except Exception as e:
            console.print(
                f"[red]Error creating agent for {model.provider.value}/{model.value}: {e}[/red]"
            )
            raise SystemExit(1) from e

    # Create arena session
    session = ArenaSession(
        agent_name=agent_name,
        models=[
            {"provider": p, "model": m} for p, m in zip(providers, models, strict=True)
        ],
    )

    # Print header
    print_header(console, agent_name)

    # Run arena chat loop
    try:
        asyncio.run(_arena_chat_loop(console, agents, providers, models, session))
    except KeyboardInterrupt:
        console.print()

    # Handle exit
    _handle_arena_exit(console, session)


def _handle_arena_exit(console: Console, session: ArenaSession) -> None:
    """Handle arena exit: prompt to save session with arrow menu."""
    console.print()

    if not session.turns:
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
