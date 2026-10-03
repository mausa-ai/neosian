"""Client-bound mailbox operations; no model work in the hook process."""

from __future__ import annotations

from typing import Any

from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.sessions import CONTINUE_HEADER, sessions_mount
from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.delivery import context, wake_text
from neosian._foundation.messaging.history import note_continuation
from neosian._foundation.record.settings import RecordSettings
from neosian._foundation.shared.prompt_assets import get_prompt, render


async def hook_mailbox(
    store: MemoryStore,
    settings: RecordSettings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    session = str(payload["session_id"])
    mailbox = Mailbox(
        MemoryConfig(store=store, mounts=settings.store.mounts),
        session=session,
        actor=f"{settings.agent}:{session}",
    )
    event = payload.get("hook_event_name")
    if event == "MailboxPoll":
        found = []
        for receipt in await mailbox.list(wake_only=True):
            item = receipt.message
            try:
                claimed = await mailbox.update(
                    item.scope, item.id, "reserve", occurrence=item.occurrence
                )
            except ValueError:
                continue
            found.append(
                {
                    "id": item.id,
                    "scope": item.scope,
                    "occurrence": item.occurrence,
                    "token": claimed.message.wake_token,
                    "text": wake_text(item),
                }
            )
            break  # One wake per poll; unread batches surface in the next context.
        return {"messages": found}
    if event == "MailboxDelivery":
        await mailbox.update(
            str(payload["scope"]),
            str(payload["message_id"]),
            "accepted" if payload.get("accepted") is True else "retry",
            occurrence=int(payload["occurrence"]),
            token=str(payload["token"]),
        )
        return {"messages": []}
    if event == "PostToolUse" and str(payload.get("tool_name", "")).endswith(
        "continue_session"
    ):
        match = CONTINUE_HEADER.search(str(payload.get("tool_response", "")))
        mount = sessions_mount(settings.store.mounts)
        if match is not None and mount is not None:
            await note_continuation(mailbox, mount.scope, match.group(1))
    inbox = await context(mailbox)
    if event == "MailboxContext":
        identity = render(get_prompt("messages.identity"), session=session).rstrip()
        inbox = "\n\n".join(part for part in (identity, inbox) if part)
    return {"context": inbox or None}
