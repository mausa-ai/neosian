"""The session `neosian chat` and `neosian playground` share (N2 slice C;
DESIGN §14.6, §35).

The session rides `Conversation` + `FileStore`: every turn persists as
it completes (a crash or ^C loses nothing), resume is `--resume <id>`,
and turns live in the home — `~/.neosian/conversations/<id>/` (DESIGN
§9.8, §22) — where an agent file that names no memory gets the
project's layout, the same place the hooks write. The view is the
Textual app in `_cli/tui`.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from rich.console import Console
from rich.text import Text

from neosian._cli.chat_mcp import serving
from neosian._cli.tui.app import Opening, SessionApp
from neosian._cli.ui import BRAND_ACCENT
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.reflection import ReflectionConfig
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import home, project_mounts
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.messaging.types import MailboxConfig
from neosian._foundation.shared.registry import provider_label, resolve_model
from neosian._foundation.shared.types import AgentConfig

_ID_STAMP = "%Y%m%d-%H%M%S"
_ID_NAME_CHARS = 64
_RESUME_IS_PATH = (
    "--resume takes a conversation id, not a path: {value!r}. Saved-session "
    "JSON files are no longer read; conversations persist under the home "
    "(~/.neosian/conversations/<id>/) — pass the id printed at chat start."
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


def chat_config(config: AgentConfig, store: FileStore) -> AgentConfig:
    """The agent file's config, with the project layout on `store` when it
    names no memory (DESIGN §22) — derived, never mutated in place. A
    config's own `memory` passes through untouched, wherever it lives.
    """
    if config.memory is not None:
        return config
    return replace(config, memory=MemoryConfig(store=store, mounts=project_mounts()))


def open_chat(
    config: AgentConfig,
    *,
    conversation_id: str,
    reflection: ReflectionConfig | None = None,
) -> Conversation:
    """The playground's Conversation on the home: one FileStore for turns
    and, unless the agent file says otherwise, for memory. `reflection`
    is the session-boundary act (§15) — a one-shot turn passes it off."""
    store = FileStore(home())
    return Conversation(
        chat_config(config, store),
        store=store,
        conversation_id=conversation_id,
        reflection=reflection,
        mailbox=MailboxConfig(),
    )


def describe_memory(config: AgentConfig) -> str:
    """One line naming where memory goes, for the chat banner."""
    mounts = project_mounts() if config.memory is None else config.memory.mounts
    return ", ".join(f"/{m.mount_path} = {m.scope}" for m in mounts)


def opening(
    config: AgentConfig,
    agent_name: str,
    convo: Conversation,
    *,
    resumed: bool,
    servers: Sequence[McpServer] = (),
) -> Opening:
    """What the session shows before the first turn: the agent, where its
    turns and memory live, how to come back, the MCP servers it opened."""
    conversation_id = convo.conversation_id
    notice = None
    if resumed and convo.messages:
        notice = f"Resumed {len(convo.messages)} messages from {conversation_id}"
    elif resumed:
        notice = f"No turns stored under {conversation_id} yet — starting fresh."
    facts = [
        ("Conversation", conversation_id),
        ("Resume", f"--resume {conversation_id}"),
        ("Home", str(home())),
        ("Memory", describe_memory(config)),
    ]
    if servers:
        facts.append(
            ("MCP", ", ".join(f"{s.name} ({len(s.tools)} tools)" for s in servers))
        )
    return Opening(agent_name, facts, notice)


async def run_chat(
    console: Console,
    config: AgentConfig,
    agent_name: str,
    *,
    conversation_id: str,
    resumed: bool,
    servers: Sequence[McpServer] = (),
) -> None:
    """Construct the store, start the conversation, open the session.

    Everything shares one event loop: a store's internal lock binds to
    the first loop that awaits it, so the store is constructed and the
    app runs inside the same `asyncio.run` — and nothing under the home
    is created before this point. The MCP servers open first and outlive
    the session; one that refuses raises before any of that (the run
    tier answers). The resume line prints once the screen is restored,
    before the close reflects (§15).
    """
    async with serving(servers, config) as config:
        try:
            convo = open_chat(config, conversation_id=conversation_id)
            await convo.start()
        except Exception as e:
            console.print(Text(f"Error starting conversation: {e}", style="red"))
            raise SystemExit(1) from e

        async with convo:
            await SessionApp(
                convo,
                opening(config, agent_name, convo, resumed=resumed, servers=servers),
                model=resolve_model(config.model),
                title=turn_title(config),
                streamed=streams(config),
            ).run_async()
            console.print(Text(f"Resume: --resume {conversation_id}", style="dim"))


def turn_title(config: AgentConfig) -> Text:
    """`provider/model`, the provider labeled by its door (DESIGN §19.2)."""
    model = resolve_model(config.model)
    title = Text()
    title.append(provider_label(model), style=BRAND_ACCENT)
    title.append("/", style="dim")
    title.append(model.value, style=BRAND_ACCENT)
    return title


def streams(config: AgentConfig) -> bool:
    """Streamed unless output guardrails demand the blocking path."""
    return config.guardrails is None or not config.guardrails.has_output_guardrails
