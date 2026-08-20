"""The `recall_turn` tool — compaction is paging, not deletion (§9.6).

Every log entry carries its turn-ref; this tool re-hydrates the verbatim
turn via the documented recall lookup, ``read_turns(after=turn-1,
limit=1)`` — there is no sixth store method. Registered lazily by
Conversation once projections exist (ledger #28), independent of memory.

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from typing import Final

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.projection import render_turn
from neosian._foundation.shared.exceptions import ConversationStoreError
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

_TOOL_NAME: Final = "recall_turn"


def create_recall_turn_tool(
    store: ConversationStore, conversation_id: str
) -> ToolFunction:
    """Create the `recall_turn` tool bound to one conversation's history."""

    @Tool(name=_TOOL_NAME, description=get_prompt("tools.recall_turn"))
    async def recall_turn(turn: int) -> ToolResult[str]:
        if turn < 1:
            return ToolResult.fail(
                f"Turn numbers start at 1; got {turn}",
                system_reminder=(
                    "Turn numbers appear in square brackets in the "
                    "conversation log, e.g. [7] or [3-6]."
                ),
            )
        try:
            turns = await store.read_turns(conversation_id, after=turn - 1, limit=1)
            if not turns or turns[0].turn != turn:
                last = await store.last_turn_number(conversation_id)
                return ToolResult.fail(
                    f"Turn {turn} does not exist",
                    system_reminder=f"This conversation has turns 1-{last}.",
                )
            return ToolResult.ok(render_turn(turns[0]))
        except ConversationStoreError as exc:
            return ToolResult.fail(f"[{exc.code}] {exc.message}")

    return recall_turn
