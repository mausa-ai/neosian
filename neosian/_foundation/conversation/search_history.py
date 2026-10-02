"""The `search_history` tool (DESIGN §32, ledger #298) and the pair it
forms with `recall_turn`.

Search finds, recall opens: a hit names a turn by conversation and
number, and `recall_turn` re-reads it verbatim. Inside a Conversation
the tool reaches what recall reaches — this conversation, the viewed
ones and the project's listed sessions (#294), never the whole store;
the server's twin (`create_search_any_tool`) is store-wide with
`conversation` narrowing, the host's duty (#295). The bound is the
tool's: `limit` above `MAX_HITS` is clamped and the footer says so; a
blank query, a limit below one or a conversation outside the reach fail
correctively; no hit at all is an answer, not a failure.

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from collections.abc import Sequence
from typing import Final

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.hits import render_hits
from neosian._foundation.conversation.recall import (
    VIEWS_REACH,
    Reach,
    create_recall_any_tool,
    in_reach,
    recall_turn_tool,
    static_reach,
)
from neosian._foundation.conversation.search import parse_query
from neosian._foundation.shared.exceptions import ConversationStoreError
from neosian._foundation.shared.prompt_assets import (
    get_prompt,
    get_prompt_params,
    render,
)
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

TOOL_NAME: Final = "search_history"
DEFAULT_HITS: Final = 10
MAX_HITS: Final = 50
PROJECT_REACH: Final = (
    "this conversation, the ones shown as views and the project's listed sessions"
)


async def search(
    store: ConversationStore, ids: Sequence[str] | None, query: str, limit: int
) -> ToolResult[str]:
    """The newest turns among `ids` (`None`: every conversation the store
    holds) matching every term of `query`, rendered as hits."""
    try:
        terms = parse_query(query)
    except ValueError:
        return ToolResult.fail(
            "Give at least one search term",
            system_reminder="A turn matches when it holds every term, case-insensitively.",
        )
    if limit < 1:
        return ToolResult.fail(f"limit must be at least 1; got {limit}")
    shown = min(limit, MAX_HITS)
    try:
        turns = await store.search_turns(query, conversations=ids, limit=shown)
    except ConversationStoreError as exc:
        return ToolResult.fail(f"[{exc.code}] {exc.message}")
    if not turns:
        listed = ", ".join(repr(term) for term in terms)
        return ToolResult.ok(
            f"No turn matches every term: {listed}. Try fewer or different terms."
        )
    footer = render(get_prompt("tools.search_history_footer"), shown=str(shown))
    return ToolResult.ok(render_hits(turns, terms, footer=footer))


def search_history_tool(
    store: ConversationStore, reach: Reach, *, where: str
) -> ToolFunction:
    """`search_history` over the conversations `reach` names, `where`
    describing them in the reminder."""

    @Tool(
        name=TOOL_NAME,
        description=get_prompt("tools.search_history"),
        params=get_prompt_params("tools.search_history_params"),
    )
    async def search_history(
        query: str, conversation: str | None = None, limit: int = DEFAULT_HITS
    ) -> ToolResult[str]:
        ids = await in_reach(reach, conversation, tool=TOOL_NAME, where=where)
        if isinstance(ids, ToolResult):
            return ids
        return await search(store, ids, query, limit)

    return search_history


def create_search_history_tool(
    store: ConversationStore,
    conversation_id: str,
    *,
    addressable: Sequence[str] = (),
) -> ToolFunction:
    """Create the `search_history` tool over one conversation's history
    plus the `addressable` ones its views name."""
    reach = static_reach((conversation_id, *addressable))
    return search_history_tool(store, reach, where=VIEWS_REACH)


def create_search_any_tool(store: ConversationStore) -> ToolFunction:
    """The server's `search_history`: every conversation the store holds,
    `conversation` narrowing to one."""

    @Tool(
        name=TOOL_NAME,
        description=get_prompt("tools.search_history_any"),
        params=get_prompt_params("tools.search_history_any_params"),
    )
    async def search_history(
        query: str, conversation: str | None = None, limit: int = DEFAULT_HITS
    ) -> ToolResult[str]:
        ids = None if conversation is None else (conversation,)
        return await search(store, ids, query, limit)

    return search_history


def project_reach(
    conversation_id: str, views: Sequence[str], sessions: Reach | None
) -> tuple[Reach, str]:
    """The Conversation's one reach and its name: this conversation, its
    views, then the project's listed sessions read live when given."""

    async def reach() -> Sequence[str]:
        listed = () if sessions is None else await sessions()
        return tuple(dict.fromkeys((conversation_id, *views, *listed)))

    return reach, (VIEWS_REACH if sessions is None else PROJECT_REACH)


def history_tools(
    store: ConversationStore,
    conversation_id: str,
    *,
    views: Sequence[str] = (),
    sessions: Reach | None = None,
) -> tuple[ToolFunction, ToolFunction]:
    """The Conversation's pair, `(recall_turn, search_history)`, over the
    project reach."""
    reach, where = project_reach(conversation_id, views, sessions)
    return (
        recall_turn_tool(store, conversation_id, reach, where=where),
        search_history_tool(store, reach, where=where),
    )


def history_any_tools(store: ConversationStore) -> tuple[ToolFunction, ToolFunction]:
    """The server's pair: `recall_turn` and `search_history` over any
    conversation the store holds."""
    return create_recall_any_tool(store), create_search_any_tool(store)
