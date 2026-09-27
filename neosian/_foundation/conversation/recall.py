"""The `recall_turn` tool — compaction is paging, not deletion (§9.6).

Every log entry carries its turn-ref; this tool re-hydrates the verbatim
turn via the documented recall lookup, ``read_turns(after=turn-1,
limit=1)`` — the sixth store method is search (§32), never recall.
Registered by Conversation with `search_history` as a pair (§32):
`conversation=` addresses a conversation in reach, `None` this one;
anything else fails correctively naming the addressable ids (ledger
#138). The reach is read when a call names a conversation, so a session
listed after the tool was built is reachable at once, and the ids never
enter the tool's description (the cached tool block stays stable). The
MCP server's twin (`create_recall_any_tool`, §21.7) has no own
conversation, so there `conversation` is required and any id the store
holds is addressable — which conversations are shareable is the host's
duty (ledger #139).

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.projection import render_turn
from neosian._foundation.shared.exceptions import (
    ConversationStoreError,
    MemoryStoreError,
)
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

_TOOL_NAME: Final = "recall_turn"
# A reminder lists the reach; a project's sessions can be hundreds.
_REMINDER_IDS: Final = 12
VIEWS_REACH: Final = "this conversation and the ones shown as views"

Reach = Callable[[], Awaitable[Sequence[str]]]


def static_reach(ids: Sequence[str]) -> Reach:
    frozen = tuple(ids)

    async def reach() -> Sequence[str]:
        return frozen

    return reach


def reach_reminder(tool: str, where: str, ids: Sequence[str]) -> str:
    shown = ", ".join(repr(i) for i in ids[:_REMINDER_IDS])
    more = len(ids) - _REMINDER_IDS
    return f"{tool} reaches {where}: {shown}{f' and {more} more' if more > 0 else ''}."


async def in_reach(
    reach: Reach, conversation: str | None, *, tool: str, where: str
) -> tuple[str, ...] | ToolResult[str]:
    """The ids a call may address: every one for `None`, the one named
    when it is in reach, else the corrective failure naming the reach."""
    try:
        ids = tuple(await reach())
    except (ConversationStoreError, MemoryStoreError) as exc:
        return ToolResult.fail(f"[{exc.code}] {exc.message}")
    if conversation is None:
        return ids
    if conversation in ids:
        return (conversation,)
    return ToolResult.fail(
        f"Conversation {conversation!r} is not addressable from here",
        system_reminder=reach_reminder(tool, where, ids),
    )


async def recall(
    store: ConversationStore, target: str, turn: int, *, label: str
) -> ToolResult[str]:
    """Turn `turn` of conversation `target`, verbatim — or the corrective
    failure, with `label` naming the conversation in the reminder."""
    if turn < 1:
        return ToolResult.fail(
            f"Turn numbers start at 1; got {turn}",
            system_reminder=(
                "Turn numbers appear in square brackets in the "
                "conversation log, e.g. [7] or [3-6]."
            ),
        )
    try:
        turns = await store.read_turns(target, after=turn - 1, limit=1)
        if not turns or turns[0].turn != turn:
            last = await store.last_turn_number(target)
            return ToolResult.fail(
                f"Turn {turn} does not exist",
                system_reminder=f"{label} has turns 1-{last}.",
            )
        return ToolResult.ok(render_turn(turns[0]))
    except ConversationStoreError as exc:
        return ToolResult.fail(f"[{exc.code}] {exc.message}")


def recall_turn_tool(
    store: ConversationStore, conversation_id: str, reach: Reach, *, where: str
) -> ToolFunction:
    """`recall_turn` over one conversation's history plus the `reach`
    a call may name, `where` describing it in the reminder."""

    @Tool(
        name=_TOOL_NAME,
        description=get_prompt("tools.recall_turn"),
        params=get_prompt_params("tools.recall_turn_params"),
    )
    async def recall_turn(
        turn: int, conversation: str | None = None
    ) -> ToolResult[str]:
        if conversation is None:
            return await recall(store, conversation_id, turn, label="This conversation")
        ids = await in_reach(reach, conversation, tool=_TOOL_NAME, where=where)
        if isinstance(ids, ToolResult):
            return ids
        return await recall(
            store, conversation, turn, label=f"Conversation {conversation!r}"
        )

    return recall_turn


def create_recall_turn_tool(
    store: ConversationStore,
    conversation_id: str,
    *,
    addressable: Sequence[str] = (),
) -> ToolFunction:
    """Create the `recall_turn` tool bound to one conversation's history
    plus the `addressable` ones its views name."""
    reach = static_reach((conversation_id, *addressable))
    return recall_turn_tool(store, conversation_id, reach, where=VIEWS_REACH)


def create_recall_any_tool(store: ConversationStore) -> ToolFunction:
    """The server's `recall_turn`: `conversation` required, any id the
    store holds — the memory server becoming the state server (§21.7)."""

    @Tool(
        name=_TOOL_NAME,
        description=get_prompt("tools.recall_turn_any"),
        params=get_prompt_params("tools.recall_turn_any_params"),
    )
    async def recall_turn(turn: int, conversation: str) -> ToolResult[str]:
        return await recall(
            store, conversation, turn, label=f"Conversation {conversation!r}"
        )

    return recall_turn
