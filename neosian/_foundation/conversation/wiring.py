"""Conversation wiring: config derivation and the agent-facing glue (§9.5).

The caller's `Agent`/`AgentConfig` is never mutated — Conversation derives
its own config: the frozen index section appended to the system prompt,
the memory tool rebuilt with the turn-ref actor closure
(`<conversation_id>#<turn>`, NP), `memory=None` on the derived config (or
the agent would register a second, unbound tool), and hooks composed with
the capture hook first. The usage folds live here too:
compaction spend rides the triggering send's response or terminal event
(ledger #29) — this module may import the agent, the compaction modules
may not (the storage-seam contract).
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.agent.events import BlockedEvent, DoneEvent
from neosian._foundation.agent.hooks import AgentHooks
from neosian._foundation.conversation.compaction import merge_usage
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.skills import create_skill_tools
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.prompt_assets import get_prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.agent.events import AgentEvent
    from neosian._foundation.agent.hooks import TurnEvent
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.compaction import CompactionResult
    from neosian._foundation.conversation.links import LinkRegistry
    from neosian._foundation.shared.types import AgentConfig, ToolFunction
    from neosian._foundation.tools.base import ToolResult

logger = logging.getLogger(__name__)

# The single-scope sugar mounts here — the path Anthropic's native
# memory_20250818 tool roots at, so N4's native wiring stays a pure
# transport swap (§9.5 ruling 13).
DEFAULT_MEMORY_MOUNT_PATH: Final = "memories"
# The board (§21, ledger #136): a mount at this path on the scope the
# caller names verbatim — shared by naming the same scope, reached from
# the shell and MCP as `--mount scope=…,path=board`.
DEFAULT_BOARD_MOUNT_PATH: Final = "board"


def resolve_memory(
    store: ConversationStore,
    base_memory: MemoryConfig | None,
    *,
    memory: MemoryConfig | None,
    mounts: Sequence[Mount] | None,
    memory_scope: str | None,
    memory_mount_path: str,
    board: str | None = None,
) -> MemoryConfig | None:
    """Resolve the exclusive memory arguments to one MemoryConfig or None;
    `board` composes with any of them (or stands alone)."""
    resolved = _resolve_exclusive(
        store,
        base_memory,
        memory=memory,
        mounts=mounts,
        memory_scope=memory_scope,
        memory_mount_path=memory_mount_path,
    )
    if board is None:
        return resolved
    mount = Mount(
        scope=board,
        mount_path=DEFAULT_BOARD_MOUNT_PATH,
        description=get_prompt("context.board"),
    )
    if resolved is None:
        return MemoryConfig(store=_as_memory_store(store, "board"), mounts=(mount,))
    return MemoryConfig(store=resolved.store, mounts=(*resolved.mounts, mount))


def _resolve_exclusive(
    store: ConversationStore,
    base_memory: MemoryConfig | None,
    *,
    memory: MemoryConfig | None,
    mounts: Sequence[Mount] | None,
    memory_scope: str | None,
    memory_mount_path: str,
) -> MemoryConfig | None:
    given = [
        name
        for name, value in (
            ("memory", memory),
            ("mounts", mounts),
            ("memory_scope", memory_scope),
        )
        if value is not None
    ]
    if len(given) > 1:
        raise ConfigurationError(
            f"At most one of memory=, mounts=, memory_scope= may be given; "
            f"got {' and '.join(given)}"
        )
    if memory is not None:
        return memory
    if mounts is not None:
        return MemoryConfig(
            store=_as_memory_store(store, "mounts"), mounts=tuple(mounts)
        )
    if memory_scope is not None:
        mount = Mount(scope=memory_scope, mount_path=memory_mount_path)
        return MemoryConfig(
            store=_as_memory_store(store, "memory_scope"), mounts=(mount,)
        )
    return base_memory


def _as_memory_store(store: ConversationStore, argument: str) -> MemoryStore:
    if not isinstance(store, MemoryStore):
        raise ConfigurationError(
            f"{argument}= requires the store to implement MemoryStore; "
            f"{type(store).__name__} does not — pass memory=MemoryConfig(...) "
            f"with its own store instead"
        )
    return store


def derive_config(
    base: AgentConfig,
    *,
    section: str | None,
    memory_config: MemoryConfig | None,
    actor: str | Callable[[], str],
    capture: Callable[[TurnEvent], None],
    extra_tools: Sequence[ToolFunction] = (),
    links: Callable[[], Sequence[LinkRegistry]] | None = None,
) -> AgentConfig:
    """Build the Conversation's own AgentConfig from the caller's.

    `actor` may be a callable resolved per command (NP): Conversation
    passes its `_turn_actor` closure so version rows carry
    `<conversation_id>#<turn>` without a per-send rebuild. `extra_tools`
    carries conversation-owned tools beyond memory — the lazily-registered
    `recall_turn` (§9.6); wiring stays dumb about them. `links` resolves
    the link registries per call: every tool then expands `[link N]`
    handles in its arguments before it runs (§23).
    """
    system_prompt = base.system_prompt
    if section is not None:
        system_prompt = f"{base.system_prompt}\n\n{section}"
    tools = list(base.tools)
    skill_dir = base.skill_dir
    if memory_config is not None:
        # native_memory itself survives the replace() below (a plain
        # field); only the tool registration needs the flag threaded.
        tools.append(
            create_memory_tool(memory_config, actor=actor, native=base.native_memory)
        )
        # The skill tools ride with memory (§24) over the already-loaded
        # directory skills plus the mounts; clearing skill_dir keeps the
        # Agent from registering a directory-only pair beside them.
        tools.extend(create_skill_tools(base.skills, memory_config))
        skill_dir = None
    tools.extend(extra_tools)
    if links is not None:
        tools = [_expanding(tool, links) for tool in tools]
    # replace() re-runs __post_init__ (re-validates; re-reads skill_dir
    # only for a memory-less conversation) — once per conversation.
    return dataclasses.replace(
        base,
        system_prompt=system_prompt,
        tools=tools,
        memory=None,
        skill_dir=skill_dir,
        hooks=_compose_hooks(base.hooks, capture),
    )


def _expanding(
    tool: ToolFunction, links: Callable[[], Sequence[LinkRegistry]]
) -> ToolFunction:
    """The tool with the handles in its arguments expanded (§23). `wraps`
    carries the tool metadata and the signature the core binds against;
    the persisted call keeps what the model said — what ran is derivable."""

    @functools.wraps(tool)
    async def expanded(**arguments: Any) -> ToolResult[Any]:
        for registry in links():
            arguments = registry.expand(arguments)
        return await tool(**arguments)

    return expanded


def as_user_message(message: str | Message) -> Message:
    """`send()` takes the USER message that opens the turn (§9.5.2)."""
    if isinstance(message, Message):
        if message.role is not Role.USER:
            raise ValueError(
                f"send() takes the USER message that opens the turn; "
                f"got role {message.role.value!r}"
            )
        return message
    return Message(role=Role.USER, content=message)


def warn_server_compaction(config: AgentConfig) -> None:
    """Log-projection replaces aged turns with log lines, dropping any
    server compaction blocks they carried — the server would then
    re-compact (and re-bill) the same span every send (ledger #49)."""
    if config.server_compaction:
        logger.warning(
            "server_compaction is on under a Conversation — the view's "
            "log-projection drops server compaction blocks at the warm "
            "boundary, paying for the same compaction repeatedly; "
            "Conversation's own paging is the supported path (§9.6)"
        )


def _compose_hooks(
    user: AgentHooks | None, capture: Callable[[TurnEvent], None]
) -> AgentHooks:
    if user is None:
        return AgentHooks(on_turn=capture)
    user_on_turn = user.on_turn
    if user_on_turn is None:
        return dataclasses.replace(user, on_turn=capture)

    async def composed(event: TurnEvent) -> None:
        # Capture first — it cannot fail; the user's hook after, with its
        # exceptions still governed by the user's own `strict` (§9.5).
        capture(event)
        result = user_on_turn(event)
        if inspect.isawaitable(result):
            await result

    return dataclasses.replace(user, on_turn=composed)


def fold_response(
    response: AgentResponse, compacted: CompactionResult | None
) -> AgentResponse:
    """Compaction spend rides the triggering send's response (ledger #29)."""
    if compacted is None or compacted.usage is None or compacted.model is None:
        return response
    return dataclasses.replace(
        response,
        usage=response.usage + compacted.usage,
        usage_by_model=merge_usage(
            response.usage_by_model, compacted.model, compacted.usage
        ),
    )


def fold_event(event: AgentEvent, compacted: CompactionResult | None) -> AgentEvent:
    """The streamed twin of `fold_response`: terminal events only."""
    if compacted is None or compacted.usage is None or compacted.model is None:
        return event
    if not isinstance(event, (DoneEvent, BlockedEvent)):
        return event
    usage = compacted.usage if event.usage is None else event.usage + compacted.usage
    return dataclasses.replace(
        event,
        usage=usage,
        usage_by_model=merge_usage(
            event.usage_by_model, compacted.model, compacted.usage
        ),
    )
