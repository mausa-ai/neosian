"""Annotations compose with history tools; original turn storage is unchanged."""

from __future__ import annotations

from functools import wraps
from typing import Any

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.hits import hit_line, snippet
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.recall import Reach
from neosian._foundation.conversation.search import parse_query
from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.delivery import context, summary
from neosian._foundation.messaging.types import InboxMessage
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import ToolResult, get_tool_metadata


async def note_continuation(mailbox: Mailbox, scope: str, parent: str) -> None:
    if mailbox.session is None:
        return
    parse_conversation_id(parent)
    mount = mailbox._mount(scope, write=True)
    if mount.edit_only:
        return
    path = f"message-links/{mailbox.session}/{parent}"
    if await mailbox.memory.store.read(scope, path) is None:
        await mailbox.memory.store.write(scope, path, parent, actor=mailbox._actor())


def annotation(item: InboxMessage) -> str:
    return summary(item) + (f"\nLatest outcome: {item.outcome}" if item.outcome else "")


def block(items: list[InboxMessage], *, budget: int = 2048) -> str:
    if not items:
        return ""
    lines = "\n\n".join(annotation(item) for item in items)
    return render(get_prompt("messages.annotations"), items=lines[:budget])


def augment_history(
    tool: ToolFunction,
    store: ConversationStore,
    mailbox: Mailbox,
    *,
    reach: Reach | None = None,
) -> ToolFunction:
    """Keep tool schemas; add message hits/annotations inside their reach."""
    metadata = get_tool_metadata(tool)
    assert metadata is not None
    name = metadata.name

    @wraps(tool)
    async def call(**arguments: Any) -> ToolResult[Any]:
        result: ToolResult[Any] = await tool(**arguments)
        if not result.success:
            return result
        receipts = await mailbox.all()
        if not receipts:
            return result
        target = arguments.get("conversation") or mailbox.session
        if name == "search_history":
            ids = None if reach is None else tuple(await reach())
            if arguments.get("conversation") is not None:
                ids = (arguments["conversation"],)
            query = str(arguments["query"])
            terms = parse_query(query)
            limit = min(int(arguments.get("limit", 10)), 50)
            turns = await store.search_turns(query, conversations=ids, limit=limit)
            items = [
                r.message for r in receipts if ids is None or r.message.about in ids
            ]
            rows = []
            for turn in turns:
                attached = [
                    i
                    for i in items
                    if i.about == turn.conversation_id
                    and i.about_turn in (None, turn.turn)
                ]
                text = hit_line(turn, terms)
                if attached:
                    text += "\n" + block(attached)
                rows.append(
                    (
                        turn.created_at,
                        f"turn:{turn.conversation_id}:{turn.turn:020}",
                        text,
                    )
                )
            for item in items:
                versions = await mailbox.history(item.scope, item.id)
                searchable = "\n".join(
                    [item.body, *(v.outcome or "" for v in versions)]
                ).lower()
                if all(term in searchable for term in terms):
                    text = annotation(item) + "\n" + snippet(searchable, terms)
                    if item.about:
                        text += f"\nAnnotation of conversation {item.about}, turn {item.about_turn or 'all'}"
                    rows.append((item.created_at, f"message:{item.id}", text))
            if rows:
                rows.sort(reverse=True)
                return ToolResult.ok("\n\n".join(row[2] for row in rows[:limit]))
            return result
        if name == "recall_turn":
            items = [
                r.message
                for r in receipts
                if r.message.about == target
                and r.message.about_turn in (None, arguments.get("turn"))
            ]
            extra = block(items)
            return result if not extra else ToolResult.ok(f"{result.data}\n\n{extra}")
        return result

    return call


async def continuation_context(mailbox: Mailbox, scope: str, target: str) -> str:
    await note_continuation(mailbox, scope, target)
    reader = Mailbox(
        mailbox.memory,
        session=target,
        actor=mailbox.actor,
        config=mailbox.config,
        clock=mailbox.clock,
    )
    pending = await context(reader)
    notes = block([r.message for r in await reader.all() if r.message.about == target])
    return "\n\n".join(part for part in (pending, notes) if part)
