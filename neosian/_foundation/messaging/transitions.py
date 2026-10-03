"""Pure, fenced lifecycle transitions; the service supplies CAS and time."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from neosian._foundation.messaging.types import Action, InboxMessage, MailboxConfig


def transition(
    item: InboxMessage,
    action: Action,
    *,
    session: str,
    occurrence: int,
    token: str | None,
    now: datetime,
    config: MailboxConfig,
    outcome: str | None = None,
    due_at: datetime | None = None,
) -> InboxMessage:
    if occurrence != item.occurrence:
        raise ValueError("stale occurrence; view the message again")
    if item.status != "open":
        if (
            action == "complete"
            and item.status == "done"
            and token == item.claim_token
            and session == item.owner
        ):
            return item
        if action == "cancel" and item.status == "cancelled":
            return item
        raise ValueError(f"message is {item.status}")
    if action == "cancel":
        return item.model_copy(update={"status": "cancelled", "outcome": outcome})
    if not item.available(now):
        raise ValueError("message is not due yet")
    if action in ("reserve", "accepted", "retry"):
        return _wake(item, action, session, token, now, config)
    if action == "ack":
        if item.claimed(now) and item.owner != session:
            raise ValueError("message is claimed by another session")
        if item.read_at is not None and not item.unread(now):
            return item
        return item.model_copy(
            update={
                "read_by": session,
                "read_at": now,
                "owner": None,
                "claim_token": None,
                "claim_until": None,
            }
        )
    if not item.actionable:
        raise ValueError("message is informational; acknowledge it instead")
    if action == "claim":
        if item.claimed(now):
            if item.owner == session:
                return item
            raise ValueError("message is claimed by another session")
        return item.model_copy(
            update={
                "owner": session,
                "claim_token": str(uuid4()),
                "claim_until": now + timedelta(seconds=config.claim_seconds),
                "read_by": session,
                "read_at": now,
            }
        )
    if not item.claimed(now) or item.owner != session or token != item.claim_token:
        raise ValueError("a current claim token owned by this session is required")
    changes: dict[str, Any] = {}
    if action == "renew":
        changes["claim_until"] = now + timedelta(seconds=config.claim_seconds)
    elif action == "complete":
        if not outcome or not outcome.strip():
            raise ValueError("completion requires an outcome")
        changes.update(status="done", outcome=outcome)
    elif action == "snooze":
        if due_at is None or due_at <= now:
            raise ValueError("snooze requires a future due time")
        if not outcome or not outcome.strip():
            raise ValueError("snooze requires the latest finding")
        changes.update(due_at=due_at, outcome=outcome, occurrence=item.occurrence + 1)
    elif action != "release":
        raise ValueError(f"unknown message action: {action}")
    if action in ("snooze", "release"):
        changes.update(
            owner=None,
            claim_token=None,
            claim_until=None,
            read_by=None,
            read_at=None,
            wake_owner=None,
            wake_token=None,
            wake_until=None,
            wake_accepted=False,
        )
    return item.model_copy(update=changes)


def _wake(
    item: InboxMessage,
    action: Action,
    session: str,
    token: str | None,
    now: datetime,
    config: MailboxConfig,
) -> InboxMessage:
    if action == "reserve":
        if item.delivery != "wake" or not item.unread(now) or item.wake_accepted:
            raise ValueError("no wake is pending")
        if item.claimed(now):
            raise ValueError("work is already claimed")
        if item.wake_until is not None and item.wake_until > now:
            raise ValueError("wake is reserved by another receiver")
        return item.model_copy(
            update={
                "wake_owner": session,
                "wake_token": str(uuid4()),
                "wake_until": now + timedelta(seconds=config.reservation_seconds),
            }
        )
    if (
        item.wake_owner != session
        or item.wake_token != token
        or item.wake_until is None
        or item.wake_until <= now
    ):
        raise ValueError("a current wake reservation is required")
    return item.model_copy(
        update={
            "wake_accepted": action == "accepted",
            "wake_owner": None,
            "wake_token": None,
            "wake_until": None,
        }
    )
