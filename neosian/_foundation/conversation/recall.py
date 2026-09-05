"""The `recall_turn` tool — compaction is paging, not deletion (§9.6).

Every log entry carries its turn-ref; this tool re-hydrates the verbatim
turn via the documented recall lookup, ``read_turns(after=turn-1,
limit=1)`` — there is no sixth store method. Registered lazily by
Conversation once projections exist (ledger #28), independent of memory,
and eagerly when it carries views (§21): `conversation=` addresses a
viewed conversation, `None` this one; anything else fails correctively
naming the addressable ids (ledger #138). The MCP server's twin
(`create_recall_any_tool`, §21.7) has no own conversation, so there
`conversation` is required and any id the store holds is addressable —
which conversations are shareable is the host's duty (ledger #139).

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from collections.abc import Sequence
from typing import Final

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.projection import render_turn
from neosian._foundation.shared.exceptions import ConversationStoreError
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

_TOOL_NAME: Final = "recall_turn"


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


def create_recall_turn_tool(
    store: ConversationStore,
    conversation_id: str,
    *,
    addressable: Sequence[str] = (),
) -> ToolFunction:
    """Create the `recall_turn` tool bound to one conversation's history
    plus the `addressable` ones its views name."""
    ids = (conversation_id, *addressable)

    @Tool(
        name=_TOOL_NAME,
        description=get_prompt("tools.recall_turn"),
        params=get_prompt_params("tools.recall_turn_params"),
    )
    async def recall_turn(
        turn: int, conversation: str | None = None
    ) -> ToolResult[str]:
        if conversation is not None and conversation not in ids:
            return ToolResult.fail(
                f"Conversation {conversation!r} is not addressable from here",
                system_reminder=(
                    "recall_turn reaches this conversation and the ones shown "
                    "as views: " + ", ".join(repr(i) for i in ids) + "."
                ),
            )
        if conversation is None:
            return await recall(store, conversation_id, turn, label="This conversation")
        return await recall(
            store, conversation, turn, label=f"Conversation {conversation!r}"
        )

    return recall_turn


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
