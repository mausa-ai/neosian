"""Conversation wiring: memory resolution and config derivation (§9.5).

The caller's `Agent`/`AgentConfig` is never mutated — Conversation derives
its own config: the frozen index section appended to the system prompt,
the memory tool rebuilt with `actor=conversation_id`, `memory=None` on the
derived config (or the agent would register a second, unbound tool), and
hooks composed with the capture hook first.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import TYPE_CHECKING, Final, cast

from neosian._foundation.agent.hooks import AgentHooks
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import SystemPrompt

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.agent.hooks import TurnEvent
    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.shared.types import AgentConfig

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
    actor: str,
    capture: Callable[[TurnEvent], None],
) -> AgentConfig:
    """Build the Conversation's own AgentConfig from the caller's."""
    system_prompt = base.system_prompt
    if section is not None:
        system_prompt = SystemPrompt(f"{base.system_prompt}\n\n{section}")
    tools = list(base.tools)
    if memory_config is not None:
        tools.append(create_memory_tool(memory_config, actor=actor))
    # replace() re-runs __post_init__ (re-reads playbook_dir, re-validates)
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
