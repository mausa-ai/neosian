"""Mailbox composition over MemoryStore; no new storage seam or daemon."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import uuid4

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.actor import parse_actor
from neosian._foundation.memory.mounts import MemoryConfig, Mount, structural, writable
from neosian._foundation.memory.sessions import parse_sessions_document, sessions_path
from neosian._foundation.messaging.transitions import transition
from neosian._foundation.messaging.types import (
    PREFIX,
    Action,
    DeliveryMode,
    InboxMessage,
    MailboxConfig,
    MessageReceipt,
    MessageTarget,
    decode,
    due_time,
    encode,
    message_path,
    utc,
)
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryConflictError,
    MemoryDocumentNotFoundError,
)

ListStatus = Literal["unread", "open", "scheduled", "closed", "all"]


class Mailbox:
    """An explicitly scoped mailbox. Session identity is never guessed."""

    def __init__(
        self,
        memory: MemoryConfig,
        *,
        session: str | None = None,
        actor: str | Callable[[], str] = "mailbox:local",
        config: MailboxConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        if session is not None:
            parse_conversation_id(session)
        self.memory = memory
        self.session = session
        self.actor = actor
        self.config = config or MailboxConfig()
        self.clock = clock or SystemClock()

    def _actor(self) -> str:
        return str(parse_actor(self.actor() if callable(self.actor) else self.actor))

    def _mount(self, scope: str, *, write: bool = False) -> Mount:
        for mount in self.memory.mounts:
            if mount.scope == scope:
                if write:
                    writable(mount)
                return mount
        raise ValueError(f"scope is not mounted: {scope}")

    async def send(
        self,
        target: MessageTarget,
        body: str,
        *,
        delivery: DeliveryMode = "next_activity",
        actionable: bool = False,
        due_at: datetime | None = None,
        delay_seconds: int | None = None,
        about: str | None = None,
        about_turn: int | None = None,
    ) -> MessageReceipt:
        structural(self._mount(target.scope, write=True))
        if not body.strip():
            raise ValueError("message body must not be empty")
        MessageTarget(target.scope, about, about_turn)
        now = utc(self.clock.now())
        item = InboxMessage(
            id=str(uuid4()),
            scope=target.scope,
            conversation=target.conversation,
            turn=target.turn,
            source=self.session,
            actor=self._actor(),
            body=body,
            created_at=now,
            due_at=due_time(now, due_at, delay_seconds),
            delivery=delivery,
            actionable=actionable or due_at is not None or delay_seconds is not None,
            about=about or target.conversation,
            about_turn=about_turn if about is not None else target.turn,
        )
        document = await self.memory.store.write(
            target.scope, message_path(item.id), encode(item), actor=self._actor()
        )
        return MessageReceipt(item, document.version)

    async def view(self, scope: str, message_id: str) -> MessageReceipt:
        self._mount(scope)
        path = message_path(message_id)
        doc = await self.memory.store.read(scope, path)
        if doc is None or doc.redacted:
            raise MemoryDocumentNotFoundError(scope, path)
        item = decode(doc.content)
        if item.scope != scope or message_path(item.id) != path:
            raise ValueError("message envelope does not match its storage address")
        return MessageReceipt(item, doc.version)

    async def history(self, scope: str, message_id: str) -> tuple[InboxMessage, ...]:
        receipt = await self.view(scope, message_id)
        rows = await self.memory.store.versions(
            scope, message_path(message_id), limit=receipt.version
        )
        return tuple(decode(row.content) for row in rows if not row.redacted)

    async def all(self, scope: str | None = None) -> tuple[MessageReceipt, ...]:
        scopes = (
            (scope,)
            if scope is not None
            else tuple(dict.fromkeys(mount.scope for mount in self.memory.mounts))
        )
        found = []
        for selected in scopes:
            self._mount(selected)
            for entry in await self.memory.store.list_documents(
                selected, prefix=PREFIX
            ):
                if entry.redacted:
                    continue
                try:
                    found.append(await self.view(selected, entry.path[len(PREFIX) :]))
                except MemoryDocumentNotFoundError:
                    continue  # deleted/redacted between listing and read
        return tuple(sorted(found, key=lambda r: (r.message.created_at, r.message.id)))

    async def lineage(self, scope: str) -> set[str]:
        self._mount(scope)
        pending = [] if self.session is None else [self.session]
        found: set[str] = set()
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            document = await self.memory.store.read(scope, sessions_path(current))
            if document is not None and not document.redacted:
                for parent in parse_sessions_document(document.content).continues:
                    parse_conversation_id(parent)
                    pending.append(parent)
            # Immediate continuation declarations can precede the recorder's Stop.
            prefix = f"message-links/{current}/"
            for entry in await self.memory.store.list_documents(scope, prefix=prefix):
                if not entry.redacted:
                    parent = entry.path[len(prefix) :]
                    parse_conversation_id(parent)
                    pending.append(parent)
        return found

    async def list(
        self,
        *,
        scope: str | None = None,
        status: ListStatus = "unread",
        limit: int = 50,
        addressed: bool = True,
        wake_only: bool = False,
    ) -> tuple[MessageReceipt, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if status not in ("unread", "open", "scheduled", "closed", "all"):
            raise ValueError("unknown inbox status")
        now = utc(self.clock.now())
        lineage: dict[str, set[str]] = {}
        found = []
        for receipt in await self.all(scope):
            item = receipt.message
            if wake_only and (
                item.delivery != "wake"
                or item.wake_accepted
                or not item.unread(now)
                or item.claimed(now)
                or (item.wake_until is not None and item.wake_until > now)
            ):
                continue
            if addressed and item.conversation is not None:
                if item.scope not in lineage:
                    lineage[item.scope] = await self.lineage(item.scope)
                if item.conversation not in lineage[item.scope]:
                    continue
            matches = {
                "all": True,
                "unread": item.unread(now),
                "open": item.available(now),
                "scheduled": item.status == "open" and item.due_at > now,
                "closed": item.status != "open",
            }
            if matches[status]:
                found.append(receipt)
        found.sort(key=lambda r: (r.message.due_at, r.message.created_at, r.message.id))
        return tuple(found[:limit])

    async def update(
        self,
        scope: str,
        message_id: str,
        action: Action,
        *,
        occurrence: int,
        token: str | None = None,
        outcome: str | None = None,
        due_at: datetime | None = None,
        delay_seconds: int | None = None,
    ) -> MessageReceipt:
        self._mount(scope, write=True)
        if self.session is None:
            raise ValueError("an explicit calling session is required")
        if not self.memory.store.supports_optimistic_concurrency:
            raise ConfigurationError(
                "mailbox updates require cross-worker optimistic concurrency"
            )
        now = utc(self.clock.now())
        due = due_time(now, due_at, delay_seconds) if action == "snooze" else None
        for attempt in range(3):
            receipt = await self.view(scope, message_id)
            item = receipt.message
            if (
                item.conversation is not None
                and item.conversation not in await self.lineage(scope)
                and (action != "cancel" or item.source != self.session)
            ):
                # The original sender may withdraw a message, but reading history
                # alone never consumes somebody else's unread message.
                raise ValueError(
                    "this session is not a recipient or explicit successor"
                )
            changed = transition(
                item,
                action,
                session=self.session,
                occurrence=occurrence,
                token=token,
                now=utc(self.clock.now()),
                config=self.config,
                outcome=outcome,
                due_at=due,
            )
            if changed == item:
                return receipt
            try:
                doc = await self.memory.store.write(
                    scope,
                    message_path(message_id),
                    encode(changed),
                    actor=self._actor(),
                    expected_version=receipt.version,
                )
                return MessageReceipt(changed, doc.version)
            except MemoryConflictError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover
