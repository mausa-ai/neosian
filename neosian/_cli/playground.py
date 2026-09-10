"""Playground command for interactive agent testing.

Provides a Rich-based chat interface for testing agent definitions.
Chat rides `Conversation` + `FileStore` (persist-per-send, resume by
id); arena mode stays in-memory in `arena.py`.
"""

import asyncio
import dataclasses

from rich.console import Console

from neosian._cli.arena import run_arena_mode
from neosian._cli.chat import new_conversation_id, resolve_resume, run_chat
from neosian._cli.models import select_provider_and_model
from neosian._foundation.agent.loader import load_agent_config
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.exceptions import ConversationIdInvalidError
from neosian._foundation.shared.types import AgentConfig, AnyModel


def menu_config(base: AgentConfig, model: AnyModel) -> AgentConfig:
    """Model override only. `replace()` re-runs `__post_init__` and
    carries every other field — the hand-rolled rebuild this replaces
    dropped nine (fallback, max_parallel_tools, max_retries,
    cache_conversation, skill_dir, the since-retired blackboard,
    client_factory,
    hooks, context_policy)."""
    return dataclasses.replace(base, model=model)


def run_playground(
    agent_path: str,
    menu: bool = False,
    arena: bool = False,
    resume: str | None = None,
) -> None:
    """Run the playground with the given agent file.

    Args:
        agent_path: Path to the agent Python file.
        menu: Show interactive menu to select provider and model.
        arena: Run in arena mode with multiple models side-by-side.
        resume: Conversation id to resume (as printed at chat start).
    """
    console = Console()

    # The library reads keys from the environment only; loading them from
    # the config file is the shell's job, done here before any client.
    from neosian._cli.providers import load_keys_into_env

    load_keys_into_env()

    # Load agent configuration
    try:
        base_config, agent_name = load_agent_config(agent_path)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e

    # Arena mode
    if arena:
        run_arena_mode(console, base_config, agent_name)
        return

    # Interactive menu override
    config = base_config
    if menu:
        require_reasoning = base_config.reasoning_effort is not None
        selected_model = select_provider_and_model(
            console, require_reasoning=require_reasoning
        )
        if selected_model is None:
            console.print("[dim]Cancelled.[/dim]")
            return
        config = menu_config(base_config, selected_model)

    # Resume by id, or open a fresh conversation
    try:
        if resume is not None:
            conversation_id = resolve_resume(resume)
        else:
            conversation_id = new_conversation_id(agent_name)
    except (ValueError, ConversationIdInvalidError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e

    # Run chat loop — the store is constructed inside, in this one event
    # loop, so --help/arena/cancel paths never create the home.
    try:
        asyncio.run(
            run_chat(
                console,
                config,
                agent_name,
                conversation_id=conversation_id,
                resumed=resume is not None,
            )
        )
    except KeyboardInterrupt:
        console.print()  # New line after ^C

    console.print(f"[dim]{PlaygroundUI.GOODBYE}[/dim]")
