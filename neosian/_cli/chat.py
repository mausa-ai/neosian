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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from neosian._cli.chat_mcp import serving
from neosian._cli.chat_shell import Consent
from neosian._cli.display import field_table
from neosian._cli.ui import BRAND_ACCENT, load_mark, load_wordmark
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.reflection import ReflectionConfig
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import home, project_mounts
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.messaging.types import MailboxConfig
from neosian._foundation.shared.constants import PlaygroundUI
from neosian._foundation.shared.registry import provider_label, resolve_model
from neosian._foundation.shared.types import AgentConfig, AnyModel

_LOCKUP_GAP: Final = 3  # columns between the mark and what sits beside it
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


def _columns(art: Text) -> int:
    return max(map(len, art.plain.splitlines()), default=0)


@dataclass(frozen=True, slots=True)
class Opening:
    """What the session says before the first turn."""

    agent: str
    facts: Sequence[tuple[str, str]]
    notice: str | None = None

    def render(self, width: int) -> RenderableType:
        """The lockup (the mark, and beside it the wordmark over the
        agent's lines), then the facts; the lines and the facts alone
        where the lockup does not fit."""
        lines: list[RenderableType] = [
            Text(
                PlaygroundUI.AGENT_LOADED.format(name=self.agent),
                style=f"bold {BRAND_ACCENT}",
            ),
            Text(PlaygroundUI.SESSION_START, style="dim"),
        ]
        if self.notice is not None:
            lines.append(Text(self.notice, style="dim"))
        facts: list[RenderableType] = [Text(), field_table(self.facts)]
        mark, wordmark = load_mark(), load_wordmark()
        needed = _columns(mark) + _LOCKUP_GAP + _columns(wordmark)
        if not (mark.plain and wordmark.plain) or width < needed:
            return Group(*lines, *facts)
        lockup = Table.grid(padding=(0, _LOCKUP_GAP))
        lockup.add_column(no_wrap=True)
        lockup.add_column()
        lockup.add_row(mark, Group(wordmark, *lines))
        return Group(lockup, *facts)


@dataclass(frozen=True, slots=True)
class Session:
    """One Conversation as the app drives it, and what it shows first."""

    convo: Conversation
    opening: Opening
    model: AnyModel
    title: Text
    streamed: bool


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


async def open_session(
    config: AgentConfig,
    agent_name: str,
    *,
    conversation_id: str,
    resumed: bool,
    servers: Sequence[McpServer] = (),
) -> Session:
    """The Conversation on the home, started, with what the app shows."""
    convo = open_chat(config, conversation_id=conversation_id)
    await convo.start()
    return Session(
        convo,
        opening(config, agent_name, convo, resumed=resumed, servers=servers),
        resolve_model(config.model),
        turn_title(config),
        streams(config),
    )


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
    tier answers). The app may move to another conversation (`/new`,
    `/resume`): the resume line names the one it left on and prints once
    the screen is restored, before the close reflects (§15).
    """
    from neosian._cli.tui.app import SessionApp  # the app imports this module

    async with serving(servers, config) as config:
        try:
            session = await open_session(
                config,
                agent_name,
                conversation_id=conversation_id,
                resumed=resumed,
                servers=servers,
            )
        except Exception as e:
            console.print(Text(f"Error starting conversation: {e}", style="red"))
            raise SystemExit(1) from e

        app = SessionApp(session, config=config, agent=agent_name, servers=servers)
        gate = config.tool_gate
        if gate is not None and isinstance(gate.approver, Consent):
            gate.approver.ask = app.confirm  # the resident agent's writes ask here
        try:
            await app.run_async()
            left_on = app.session.convo.conversation_id
            console.print(Text(f"Resume: --resume {left_on}", style="dim"))
        finally:
            await app.session.convo.aclose()


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
