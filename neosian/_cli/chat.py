"""Conversation-backed chat for the playground (N2 slice C).

The chat loop rides `Conversation` + `FileStore`: every turn persists as
it completes (a crash or ^C loses nothing), resume is `--resume <id>`,
and turns live under the working directory — project-local, in
`.neosian/conversations/<id>/` (DESIGN §9.8).
"""

import time
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from neosian._cli.stream import stream_turn
from neosian._cli.ui import format_args, format_elapsed_time, print_header
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.llm.base import text_of
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.registry import provider_label
from neosian._foundation.shared.types import AgentConfig

_STORE_DIR = ".neosian"  # FileStore root under cwd; turns in conversations/<id>/
_ID_STAMP = "%Y%m%d-%H%M%S"
_ID_NAME_CHARS = 64
_RESUME_IS_PATH = (
    "--resume takes a conversation id, not a path: {value!r}. Saved-session "
    "JSON files are no longer read; conversations persist under "
    ".neosian/conversations/<id>/ — pass the id printed at chat start."
)


def new_conversation_id(agent_name: str, *, now: datetime | None = None) -> str:
    """Timestamp plus slugged agent name, forced into the §9.4 grammar.

    The stamp prefix guarantees a non-empty id that is never a bare
    `.`/`..` and stays well under 128 chars, whatever the agent name.
    """
    stamp = (now if now is not None else datetime.now(UTC)).strftime(_ID_STAMP)
    slug = "".join(
        c if c.isascii() and (c.isalnum() or c in "_.-") else "-" for c in agent_name
    )
    slug = slug[:_ID_NAME_CHARS].strip("-.")
    raw = f"{stamp}-{slug}" if slug else stamp
    return str(parse_conversation_id(raw))


def resolve_resume(value: str) -> str:
    """--resume argument -> conversation id; refuses the legacy path form.

    The path check runs first: `last.json` matches the id grammar (dots
    are legal), so grammar validation alone would silently open an empty
    conversation named after the file.
    """
    if "/" in value or "\\" in value or value.endswith(".json"):
        raise ValueError(_RESUME_IS_PATH.format(value=value))
    return str(parse_conversation_id(value))


def open_chat(config: AgentConfig, *, root: Path, conversation_id: str) -> Conversation:
    """The playground's Conversation: a cwd-local FileStore for turns.

    The agent file's own `config.memory` passes through untouched —
    Conversation re-wires its tool with `actor=conversation_id`; this
    store is only the turn seam, so a memory store (if any) stays
    wherever the agent file put it.
    """
    store = FileStore(root / _STORE_DIR)
    return Conversation(config, store=store, conversation_id=conversation_id)


async def run_chat(
    console: Console,
    config: AgentConfig,
    agent_name: str,
    *,
    conversation_id: str,
    root: Path,
    resumed: bool,
) -> None:
    """Construct the store, start the conversation, run the loop.

    Everything shares one event loop: a store's internal lock binds to
    the first loop that awaits it, so the store is constructed and used
    inside the same `asyncio.run` — and nothing under `root` is created
    before this point.
    """
    try:
        convo = open_chat(config, root=root, conversation_id=conversation_id)
        await convo.start()
    except Exception as e:
        console.print(f"[red]Error starting conversation: {e}[/red]")
        raise SystemExit(1) from e

    print_header(console, agent_name)
    if resumed and convo.messages:
        console.print(
            f"[dim]Resumed {len(convo.messages)} messages from "
            f"{conversation_id}[/dim]"
        )
    elif resumed:
        console.print(
            f"[dim]No turns stored under {conversation_id} yet — "
            f"starting fresh.[/dim]"
        )
    console.print(
        f"[dim]Conversation: {conversation_id}  "
        f"(resume: --resume {conversation_id})[/dim]\n"
    )
    async with convo:
        await _chat_loop(console, convo, config)


def turn_title(config: AgentConfig) -> Text:
    """`provider/model`, the provider labeled by its door (DESIGN §19.2)."""
    title = Text()
    title.append(provider_label(config.model), style="cyan")
    title.append("/", style="dim")
    title.append(config.model.value, style="blue")
    return title


def streams(config: AgentConfig) -> bool:
    """Streamed unless output guardrails demand the blocking path."""
    return config.guardrails is None or not config.guardrails.has_output_guardrails


async def _chat_loop(
    console: Console, convo: Conversation, config: AgentConfig
) -> None:
    """Run the main chat loop; persistence rides `Conversation.send`."""
    title = turn_title(config)
    streamed = streams(config)
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

        start_time = time.perf_counter()
        try:
            if streamed:
                await stream_turn(
                    console, convo, user_input, model=config.model, title=title
                )
                console.print()
                continue
            with console.status(f"[dim]{PlaygroundUI.THINKING}[/dim]"):
                response = await convo.send(user_input)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            continue
        _render_response(console, response, title, time.perf_counter() - start_time)


def _render_response(
    console: Console, response: AgentResponse, title: Text, elapsed_time: float
) -> None:
    """The blocking path's rendering: the finished turn, panel by panel."""
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
            blocked_text.append(PlaygroundUI.GUARDRAIL_INPUT_BLOCKED, style="bold red")
        else:
            blocked_text.append(PlaygroundUI.GUARDRAIL_OUTPUT_BLOCKED, style="bold red")

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
        console.print(format_elapsed_time(elapsed_time), style="dim")
        console.print()

        # Blocked turns persist nothing (§9.5.5) — the panel is the log.
        return

    # Display tool calls if any
    for i, tool_call in enumerate(response.tool_calls_made):
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
        console.print(
            Panel(
                Markdown(assistant_text),
                title=title,
                border_style="blue",
            )
        )

    # Display response time
    console.print(format_elapsed_time(elapsed_time), style="dim")
    console.print()
