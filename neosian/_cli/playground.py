"""Playground command for interactive agent testing.

Provides a Rich-based chat interface for testing agent definitions.
"""

import asyncio
import importlib.resources
import os
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

from neosian._cli.config import get_api_key
from neosian._cli.session import ArenaModelResponse, ArenaSession, Session
from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.loader import load_agent_config
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.shared.constants import ArenaUI, Assets, Config, PlaygroundUI
from neosian._foundation.shared.types import (
    DEFAULT_MODELS,
    AgentConfig,
    Model,
    Provider,
)


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

    # Anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        anthropic_key = get_api_key(Config.ANTHROPIC_API_KEY)
        if anthropic_key:
            os.environ["ANTHROPIC_API_KEY"] = anthropic_key

    # Cerebras
    if not os.environ.get("CEREBRAS_API_KEY"):
        cerebras_key = get_api_key(Config.CEREBRAS_API_KEY)
        if cerebras_key:
            os.environ["CEREBRAS_API_KEY"] = cerebras_key


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


# Models hidden from the interactive picker (special-purpose, e.g. guardrails).
_HIDDEN_MODELS: frozenset[Model] = frozenset({Model.GROQ_GPT_OSS_SAFEGUARD_20B})

# Optional flavor text appended to a model's display name. The "(default)"
# marker is derived from DEFAULT_MODELS, not baked in here.
_MODEL_NOTES: dict[Model, str] = {
    Model.GROQ_QWEN3_6_27B: "preview",
    Model.GPT_5_NANO: "fastest",
    Model.GPT_5_MINI: "balanced",
    Model.GPT_5_1: "best for coding",
    Model.GPT_5_PRO: "most precise",
    Model.CLAUDE_OPUS_5: "most capable",
    Model.CLAUDE_SONNET_5: "balanced",
    Model.CLAUDE_HAIKU_4_5: "fastest",
    Model.CEREBRAS_GPT_OSS_120B: "fastest 120B",
    Model.CEREBRAS_GEMMA_4_31B: "preview",
}


def _display_name(model: Model) -> str:
    """Build the picker label for a model from the registry."""
    notes: list[str] = []
    if DEFAULT_MODELS.get(model.provider) is model:
        notes.append("default")
    note = _MODEL_NOTES.get(model)
    if note is not None:
        notes.append(note)
    return f"{model.value} ({', '.join(notes)})" if notes else model.value


def _get_models_for_provider(
    provider: Provider, *, require_reasoning: bool = False
) -> list[tuple[Model, str]]:
    """Get available models for a provider, derived from the Model registry.

    Args:
        provider: The LLM provider.
        require_reasoning: If True, only return models that support reasoning.

    Returns:
        List of (Model, display_name) tuples, default model first.
    """
    models = [
        m
        for m in Model
        if m.provider is provider
        and m not in _HIDDEN_MODELS
        and (not require_reasoning or m.supports_reasoning)
    ]
    default = DEFAULT_MODELS.get(provider)
    models.sort(key=lambda m: m is not default)  # stable: default first
    return [(m, _display_name(m)) for m in models]


_ALL_PROVIDERS: list[tuple[Provider, str]] = [
    (Provider.GROQ, "Groq (fastest inference)"),
    (Provider.OPENAI, "OpenAI"),
    (Provider.ANTHROPIC, "Anthropic (Claude)"),
    (Provider.CEREBRAS, "Cerebras (fast open models)"),
]


def _get_available_providers(
    *,
    require_reasoning: bool = False,
) -> list[tuple[Provider, str]]:
    """Get providers that have at least one available model.

    Args:
        require_reasoning: If True, only include providers with reasoning models.

    Returns:
        Filtered list of (Provider, display_name) tuples.
    """
    return [
        (p, label)
        for p, label in _ALL_PROVIDERS
        if _get_models_for_provider(p, require_reasoning=require_reasoning)
    ]


def _select_provider_and_model(
    console: Console, *, require_reasoning: bool = False
) -> Model | None:
    """Show interactive menu to select provider and model.

    Args:
        console: Rich console for output.
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        Selected Model or None if cancelled.
    """
    providers = _get_available_providers(require_reasoning=require_reasoning)
    if not providers:
        return None

    while True:
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
        models = _get_models_for_provider(
            selected_provider, require_reasoning=require_reasoning
        )
        if not models:
            return None

        console.print(f"\n[bold]Select Model ({selected_provider.value}):[/bold]")
        model_names = [m[1] for m in models] + ["← Back"]
        model_menu = TerminalMenu(model_names, cursor_index=0)
        model_choice = model_menu.show()

        if model_choice is None or model_choice == len(models):
            # Back to provider selection
            continue

        selected_model: Model = models[model_choice][0]
        return selected_model


def _select_provider_and_model_labeled(
    console: Console, label: str, *, require_reasoning: bool = False
) -> Model | None:
    """Show interactive menu to select provider and model with a label.

    Args:
        console: Rich console for output.
        label: Label to show (e.g., "Model 1").
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        Selected Model or None if cancelled.
    """
    providers = _get_available_providers(require_reasoning=require_reasoning)
    if not providers:
        return None

    while True:
        console.print(f"\n[bold]{ArenaUI.SELECT_PROVIDER.format(label=label)}[/bold]")
        provider_menu = TerminalMenu([p[1] for p in providers], cursor_index=0)
        provider_choice = provider_menu.show()

        if provider_choice is None:
            return None

        selected_provider = providers[provider_choice][0]

        models = _get_models_for_provider(
            selected_provider, require_reasoning=require_reasoning
        )
        if not models:
            return None

        console.print(
            f"\n[bold]{ArenaUI.SELECT_MODEL.format(label=label, provider=selected_provider.value)}[/bold]"
        )
        model_names = [m[1] for m in models] + ["← Back"]
        model_menu = TerminalMenu(model_names, cursor_index=0)
        model_choice = model_menu.show()

        if model_choice is None or model_choice == len(models):
            # Back to provider selection
            continue

        labeled_model: Model = models[model_choice][0]
        return labeled_model


def _select_arena_models(
    console: Console, *, require_reasoning: bool = False
) -> list[Model] | None:
    """Select models for arena mode.

    Args:
        console: Rich console for output.
        require_reasoning: If True, only show models that support reasoning.

    Returns:
        List of Model enums or None if cancelled.
    """
    # Select count
    console.print(f"\n[bold]{ArenaUI.SELECT_COUNT}[/bold]")
    count_menu = TerminalMenu(list(ArenaUI.COUNT_OPTIONS), cursor_index=0)
    count_choice = count_menu.show()

    if count_choice is None:
        return None

    model_count = int(ArenaUI.COUNT_OPTIONS[count_choice])

    # Select each model
    selections: list[Model] = []
    for i in range(model_count):
        label = ArenaUI.MODEL_LABEL.format(n=i + 1)
        selection = _select_provider_and_model_labeled(
            console, label, require_reasoning=require_reasoning
        )
        if selection is None:
            return None
        selections.append(selection)

    return selections


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


async def _run_arena_model(
    agent: Agent,
    messages: list[Message],
    provider: str,
    model: str,
) -> ArenaModelResult:
    """Run a single model and collect results (stateless, for backward compatibility).

    Args:
        agent: The agent to run.
        messages: Message history.
        provider: Provider ID for display.
        model: Model ID for display.

    Returns:
        ArenaModelResult with response data.
    """
    start_time = time.perf_counter()

    try:
        response = await agent.run(messages, stream=False)
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
        tool_text.append(f"({_format_args(tool_call.arguments)})", style="dim")

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


def run_playground(agent_path: str, menu: bool = False, arena: bool = False) -> None:
    """Run the playground with the given agent file.

    Args:
        agent_path: Path to the agent Python file.
        menu: Show interactive menu to select provider and model.
        arena: Run in arena mode with multiple models side-by-side.
    """
    console = Console()

    # Load credentials from config file if not in environment
    _load_credentials_from_config()

    # Load agent configuration
    try:
        base_config, agent_name = load_agent_config(agent_path)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e

    # Arena mode
    if arena:
        _run_arena_mode(console, base_config, agent_name)
        return

    # Interactive menu override
    config = base_config
    if menu:
        require_reasoning = base_config.reasoning_effort is not None
        selected_model = _select_provider_and_model(
            console, require_reasoning=require_reasoning
        )
        if selected_model is None:
            console.print("[dim]Cancelled.[/dim]")
            return

        config = AgentConfig(
            system_prompt=base_config.system_prompt,
            tools=base_config.tools,
            model=selected_model,
            enable_todo=base_config.enable_todo,
            guardrails=base_config.guardrails,
            reasoning_effort=base_config.reasoning_effort,
            max_output_tokens=base_config.max_output_tokens,
        )

    # Create agent from config
    try:
        agent = Agent(config=config)
    except Exception as e:
        console.print(f"[red]Error creating agent: {e}[/red]")
        raise SystemExit(1) from e

    # Determine provider and model for display
    display_provider = config.model.provider.value
    display_model = config.model.value

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


def _run_arena_mode(
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
    selected_models = _select_arena_models(console, require_reasoning=require_reasoning)
    if selected_models is None:
        console.print("[dim]Cancelled.[/dim]")
        return

    # Create agents
    agents: list[Agent] = []
    providers: list[str] = []
    models: list[str] = []

    for model in selected_models:
        config = AgentConfig(
            system_prompt=base_config.system_prompt,
            tools=base_config.tools,
            model=model,
            enable_todo=base_config.enable_todo,
            guardrails=base_config.guardrails,
            reasoning_effort=base_config.reasoning_effort,
            max_output_tokens=base_config.max_output_tokens,
        )
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
    _print_header(console, agent_name)

    # Run arena chat loop
    try:
        asyncio.run(_arena_chat_loop(console, agents, providers, models, session))
    except KeyboardInterrupt:
        console.print()

    # Handle exit
    _handle_arena_exit(console, session)


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
    """Run the main chat loop.

    Uses AgentSession to cache HTTP clients across messages for reduced latency.
    """
    # Use session to cache HTTP clients across the entire chat loop
    async with agent.session() as agent_session:
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

            # Start timing
            start_time = time.perf_counter()

            # Show thinking indicator
            with console.status(f"[dim]{PlaygroundUI.THINKING}[/dim]"):
                # Build message history for agent (add current input)
                messages = [
                    Message(
                        role=Role.USER if m.role == Role.USER else Role.ASSISTANT,
                        content=m.content or "",
                    )
                    for m in session.get_messages()
                ]
                messages.append(Message(role=Role.USER, content=user_input))

                # Get response using cached client
                try:
                    response = await agent_session.run(messages, stream=False)
                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    continue

            # Calculate elapsed time
            elapsed_time = time.perf_counter() - start_time

            # Display guardrail result if present (shows parallel execution)
            if response.guardrail_result:
                gr_result = response.guardrail_result
                guard_text = Text()

                if gr_result.safe:
                    guard_text.append("safe", style="green")
                else:
                    guard_text.append("flagged", style="red")
                    if gr_result.policy_rationale:
                        guard_text.append(
                            f" ({gr_result.policy_rationale})",
                            style="yellow",
                        )

                console.print(Panel(guard_text, title="Guard", border_style="dim"))

            # Display guardrail blocked if applicable
            if response.blocked and response.guardrail_result:
                gr_result = response.guardrail_result
                blocked_text = Text()

                # Determine message based on where blocked
                if gr_result.flagged_at == "input":
                    blocked_text.append(
                        PlaygroundUI.GUARDRAIL_INPUT_BLOCKED, style="bold red"
                    )
                else:
                    blocked_text.append(
                        PlaygroundUI.GUARDRAIL_OUTPUT_BLOCKED, style="bold red"
                    )

                # Add rationale if present
                if gr_result.policy_rationale:
                    blocked_text.append("\n")
                    blocked_text.append(
                        PlaygroundUI.GUARDRAIL_RATIONALE.format(
                            rationale=gr_result.policy_rationale
                        ),
                        style="dim",
                    )

                console.print(
                    Panel(
                        blocked_text,
                        title=PlaygroundUI.GUARDRAIL_BLOCKED_LABEL,
                        border_style="red",
                    )
                )
                console.print(_format_elapsed_time(elapsed_time), style="dim")
                console.print()

                # Store blocked message separately (not in LLM history)
                session.add_blocked_message(user_input, gr_result)
                continue

            # Add user message to session (only if not blocked)
            session.add_user_message(user_input)

            # Display tool calls if any
            for i, tool_call in enumerate(response.tool_calls_made):
                tool_text = Text()
                tool_text.append(f"{tool_call.name}", style="yellow")
                tool_text.append(f"({_format_args(tool_call.arguments)})", style="dim")

                if i < len(response.tool_results):
                    result = response.tool_results[i]
                    if result.success:
                        tool_text.append(" → ", style="dim")
                        tool_text.append(str(result.data), style="green")
                    else:
                        tool_text.append(" → ", style="dim")
                        tool_text.append(str(result.error), style="red")

                console.print(
                    Panel(
                        tool_text,
                        title=PlaygroundUI.TOOL_CALL_LABEL,
                        border_style="yellow",
                    )
                )

            # Display reasoning if present
            if response.message.reasoning:
                console.print(
                    Panel(
                        Markdown(response.message.reasoning),
                        title="Reasoning",
                        border_style="dim",
                    )
                )

            # Display assistant response
            assistant_text = text_of(response.message)
            if assistant_text:
                session.add_assistant_message(assistant_text)
                title = Text()
                title.append(provider, style="cyan")
                title.append("/", style="dim")
                title.append(model, style="blue")
                console.print(
                    Panel(
                        Markdown(assistant_text),
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

    if not session.messages and not session.blocked_messages:
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
