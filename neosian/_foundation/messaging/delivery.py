"""Bounded context injection and an explicitly owned wake receiver."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.types import InboxMessage, MessageReceipt
from neosian._foundation.shared.prompt_assets import get_prompt, render

logger = logging.getLogger(__name__)


def summary(item: InboxMessage) -> str:
    return (
        f"[{item.id} occurrence={item.occurrence} scope={item.scope} "
        f"from={item.actor} at={item.created_at.isoformat()} "
        f"status={item.status} actionable={item.actionable}]\n{item.body}"
    )


async def context(mailbox: Mailbox, *, budget: int | None = None) -> str:
    """Rendering never marks anything read, including overflow or failure."""
    limit = mailbox.config.context_chars if budget is None else budget
    items = await mailbox.list()
    if not items:
        return ""
    template = get_prompt("messages.context")
    frame = render(template, session=mailbox.session or "unbound", items="")
    room = max(0, limit - len(frame))
    lines: list[str] = []
    for receipt in items:
        line = summary(receipt.message)
        if sum(len(s) + 1 for s in lines) + len(line) > room:
            line = line[: max(0, room - sum(len(s) + 1 for s in lines) - 1)]
            if line:
                lines.append(line)
            break
        lines.append(line)
    return render(
        template, session=mailbox.session or "unbound", items="\n".join(lines)
    )[:limit]


def wake_text(item: InboxMessage, *, budget: int = 2048) -> str:
    template = render(
        get_prompt("messages.wake"),
        id=item.id,
        occurrence=str(item.occurrence),
        scope=item.scope,
        actor=item.actor,
    )
    room = max(0, budget - len(template) + len("{{body}}"))
    return render(template, body=item.body[:room])[:budget]


async def receive_once(
    mailbox: Mailbox,
    deliver: Callable[[InboxMessage], Awaitable[bool]],
) -> tuple[MessageReceipt, ...]:
    """Deliver eligible wake items. True means accepted, never acknowledged.

    A callback must deduplicate (id, occurrence) if its transport can accept
    and then lose the response. Reservations recover interrupted attempts.
    """
    accepted = []
    for receipt in await mailbox.list(wake_only=True):
        item = receipt.message
        try:
            reserved = await mailbox.update(
                item.scope, item.id, "reserve", occurrence=item.occurrence
            )
        except ValueError:
            continue
        delivered = await deliver(reserved.message)
        result = await mailbox.update(
            item.scope,
            item.id,
            "accepted" if delivered else "retry",
            occurrence=item.occurrence,
            token=reserved.message.wake_token,
        )
        if delivered:
            accepted.append(result)
    return tuple(accepted)


async def listen(
    mailbox: Mailbox,
    deliver: Callable[[InboxMessage], Awaitable[bool]],
) -> None:
    """Run until cancelled; the caller owns the task and model execution."""
    if mailbox.session is None:
        raise ValueError("wake receivers require an explicit session")
    while True:
        try:
            await receive_once(mailbox, deliver)
        except Exception:
            logger.warning(
                "mailbox receiver failed; pending messages retained", exc_info=True
            )
        await asyncio.sleep(mailbox.config.poll_seconds)
