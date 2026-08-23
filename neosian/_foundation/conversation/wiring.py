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
import inspect
from typing import TYPE_CHECKING, Final, cast

from neosian._foundation.agent.events import BlockedEvent, DoneEvent
from neosian._foundation.agent.hooks import AgentHooks
from neosian._foundation.conversation.compaction import merge_usage
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import SystemPrompt

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.agent.events import AgentEvent
    from neosian._foundation.agent.hooks import TurnEvent
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.compaction import CompactionResult
    from neosian._foundation.shared.types import AgentConfig, ToolFunction

# The single-scope sugar mounts here — the path Anthropic's native
# memory_20250818 tool roots at, so N4's native wiring stays a pure
# transport swap (§9.5 ruling 13).
DEFAULT_MEMORY_MOUNT_PATH: Final = "memories"


def resolve_memory(
    store: ConversationStore,
    base_memory: object,
    *,
    memory: MemoryConfig | None,
    mounts: Sequence[Mount] | None,
    memory_scope: str | None,
    memory_mount_path: str,
) -> MemoryConfig | None:
    """Resolve the exclusive memory arguments to one MemoryConfig or None."""
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
    return cast("MemoryConfig | None", base_memory)


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
) -> AgentConfig:
    """Build the Conversation's own AgentConfig from the caller's.

    `actor` may be a callable resolved per command (NP): Conversation
    passes its `_turn_actor` closure so version rows carry
    `<conversation_id>#<turn>` without a per-send rebuild. `extra_tools`
    carries conversation-owned tools beyond memory — the lazily-registered
    `recall_turn` (§9.6); wiring stays dumb about them.
    """
    system_prompt = base.system_prompt
    if section is not None:
        system_prompt = SystemPrompt(f"{base.system_prompt}\n\n{section}")
    tools = list(base.tools)
    if memory_config is not None:
        # native_memory itself survives the replace() below (a plain
        # field); only the tool registration needs the flag threaded.
        tools.append(
            create_memory_tool(memory_config, actor=actor, native=base.native_memory)
        )
    tools.extend(extra_tools)
    # replace() re-runs __post_init__ (re-reads skill_dir, re-validates)
    # — once per conversation, at start().
    return dataclasses.replace(
        base,
        system_prompt=system_prompt,
        tools=tools,
        memory=None,
        hooks=_compose_hooks(base.hooks, capture),
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
