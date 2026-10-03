"""Mailbox values and their versioned document codec."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.scope import parse_scope

PREFIX = "messages/"
DeliveryMode = Literal["next_activity", "wake"]
MessageStatus = Literal["open", "done", "cancelled"]
Action = Literal[
    "ack",
    "claim",
    "renew",
    "release",
    "complete",
    "snooze",
    "cancel",
    "reserve",
    "accepted",
    "retry",
]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("mailbox timestamps must be timezone-aware")
    return value.astimezone(UTC)


def message_path(message_id: str) -> str:
    return PREFIX + str(UUID(message_id))


@dataclass(frozen=True, slots=True)
class MessageTarget:
    scope: str
    conversation: str | None = None
    turn: int | None = None

    def __post_init__(self) -> None:
        parse_scope(self.scope)
        if self.conversation is not None:
            parse_conversation_id(self.conversation)
        if self.turn is not None and (self.turn < 1 or self.conversation is None):
            raise ValueError("a turn reference needs a conversation and turn >= 1")


@dataclass(frozen=True, slots=True)
class MailboxConfig:
    context_chars: int = 2048
    claim_seconds: int = 1800
    poll_seconds: float = 5.0
    reservation_seconds: int = 60

    def __post_init__(self) -> None:
        if (
            min(
                self.context_chars,
                self.claim_seconds,
                self.poll_seconds,
                self.reservation_seconds,
            )
            <= 0
        ):
            raise ValueError("mailbox limits must be positive")


class InboxMessage(BaseModel):
    """One canonical message. Store versions retain every prior outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = 1
    id: str
    scope: str
    conversation: str | None = None
    turn: int | None = None
    source: str | None = None
    about: str | None = None
    about_turn: int | None = None
    actor: str
    body: str
    created_at: datetime
    due_at: datetime
    delivery: DeliveryMode = "next_activity"
    actionable: bool = False
    occurrence: int = Field(default=1, ge=1)
    status: MessageStatus = "open"
    read_by: str | None = None
    read_at: datetime | None = None
    owner: str | None = None
    claim_token: str | None = None
    claim_until: datetime | None = None
    outcome: str | None = None
    wake_owner: str | None = None
    wake_token: str | None = None
    wake_until: datetime | None = None
    wake_accepted: bool = False

    @field_validator("created_at", "due_at", "read_at", "claim_until", "wake_until")
    @classmethod
    def timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else utc(value)

    def available(self, now: datetime) -> bool:
        return self.status == "open" and self.due_at <= utc(now)

    def claimed(self, now: datetime) -> bool:
        return (
            self.owner is not None
            and self.claim_until is not None
            and self.claim_until > now
        )

    def unread(self, now: datetime) -> bool:
        expired = self.owner is not None and not self.claimed(now)
        return self.available(now) and (self.read_at is None or expired)


@dataclass(frozen=True, slots=True)
class MessageReceipt:
    message: InboxMessage
    version: int


def due_time(
    now: datetime, due_at: datetime | None, delay_seconds: int | None
) -> datetime:
    now = utc(now)
    if due_at is not None and delay_seconds is not None:
        raise ValueError("give due_at or delay_seconds, not both")
    if delay_seconds is not None:
        if delay_seconds <= 0:
            raise ValueError("delay_seconds must be positive")
        return now + timedelta(seconds=delay_seconds)
    return now if due_at is None else utc(due_at)


def encode(message: InboxMessage) -> str:
    # JSON is also YAML, and keeps the envelope independent of YAML tag rules.
    return message.model_dump_json(indent=2) + "\n"


def decode(content: str) -> InboxMessage:
    message = InboxMessage.model_validate_json(content)
    message_path(message.id)
    MessageTarget(message.scope, message.conversation, message.turn)
    MessageTarget(message.scope, message.about, message.about_turn)
    if message.source is not None:
        parse_conversation_id(message.source)
    return message
