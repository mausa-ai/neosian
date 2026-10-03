"""Delivery, ownership and reminder resets against actual storage."""

import asyncio
from datetime import datetime, timedelta

import pytest

from neosian import MemoryConfig, Mount
from neosian._foundation.messaging.delivery import context
from neosian._foundation.messaging.history import note_continuation
from neosian._foundation.messaging.types import message_path
from neosian._foundation.shared.exceptions import MemoryDocumentNotFoundError
from neosian.messaging import Mailbox, MailboxConfig, MessageTarget

from .conftest import SCOPE, Clock


async def test_delivery_repeats_until_acknowledged(mailbox: Mailbox) -> None:
    receipt = await mailbox.send(MessageTarget(SCOPE, "recipient"), "migration ready")
    assert "migration ready" in await context(mailbox)
    assert "migration ready" in await context(mailbox)
    assert (await mailbox.view(SCOPE, receipt.message.id)).version == 1
    result = await mailbox.update(SCOPE, receipt.message.id, "ack", occurrence=1)
    assert result.message.read_by == "recipient"
    assert await context(mailbox) == ""
    again = await mailbox.update(SCOPE, receipt.message.id, "ack", occurrence=1)
    assert again.version == result.version


async def test_reminder_snooze_and_stale_completion(
    mailbox: Mailbox, clock: Clock
) -> None:
    receipt = await mailbox.send(
        MessageTarget(SCOPE), "check upstream fix", delay_seconds=15 * 86400
    )
    item = receipt.message
    assert await mailbox.list() == ()
    assert len(await mailbox.list(status="scheduled")) == 1
    with pytest.raises(ValueError, match="not due"):
        await mailbox.update(SCOPE, item.id, "claim", occurrence=1)
    clock.advance(15 * 86400)
    assert len(await mailbox.list()) == 1
    read = await mailbox.update(SCOPE, item.id, "ack", occurrence=1)
    assert read.message.status == "open"
    assert len(await mailbox.list(status="open")) == 1
    claimed = await mailbox.update(SCOPE, item.id, "claim", occurrence=1)
    token = claimed.message.claim_token
    reset = await mailbox.update(
        SCOPE,
        item.id,
        "snooze",
        occurrence=1,
        token=token,
        delay_seconds=14 * 86400,
        outcome="checked; still no fix",
    )
    assert reset.message.id == item.id
    assert reset.message.due_at == clock.now() + timedelta(days=14)
    assert reset.message.occurrence == 2 and reset.message.owner is None
    assert reset.message.read_at is None and await mailbox.list() == ()
    for action in ("ack", "complete"):
        with pytest.raises(ValueError, match="stale occurrence"):
            await mailbox.update(SCOPE, item.id, action, occurrence=1, token=token)
    clock.advance(14 * 86400)
    assert len(await mailbox.list()) == 1
    claimed = await mailbox.update(SCOPE, item.id, "claim", occurrence=2)
    done = await mailbox.update(
        SCOPE,
        item.id,
        "complete",
        occurrence=2,
        token=claimed.message.claim_token,
        outcome="released",
    )
    assert done.message.status == "done" and await mailbox.list() == ()
    assert len(await mailbox.list(status="closed")) == 1
    assert any(
        v.outcome == "checked; still no fix"
        for v in await mailbox.history(SCOPE, item.id)
    )
    assert (
        await mailbox.update(
            SCOPE, item.id, "complete", occurrence=2, token=claimed.message.claim_token
        )
    ).version == done.version


async def test_claim_race_lease_recovery_and_fencing(
    memory: MemoryConfig, clock: Clock
) -> None:
    left = Mailbox(memory, session="left", clock=clock)
    right = Mailbox(memory, session="right", clock=clock)
    item = (await left.send(MessageTarget(SCOPE), "one check", actionable=True)).message
    outcomes = await asyncio.gather(
        left.update(SCOPE, item.id, "claim", occurrence=1),
        right.update(SCOPE, item.id, "claim", occurrence=1),
        return_exceptions=True,
    )
    assert sum(isinstance(o, ValueError) for o in outcomes) == 1
    current = (await left.view(SCOPE, item.id)).message
    winner, loser = (left, right) if current.owner == "left" else (right, left)
    renewed = await winner.update(
        SCOPE, item.id, "renew", occurrence=1, token=current.claim_token
    )
    assert renewed.message.owner == current.owner
    clock.advance(1801)
    assert len(await loser.list()) == 1
    await loser.update(SCOPE, item.id, "claim", occurrence=1)
    with pytest.raises(ValueError, match="current claim"):
        await winner.update(
            SCOPE,
            item.id,
            "complete",
            occurrence=1,
            token=current.claim_token,
            outcome="late",
        )


async def test_successors_not_unrelated_readers(
    memory: MemoryConfig, clock: Clock
) -> None:
    sender = Mailbox(memory, session="sender", clock=clock)
    item = (await sender.send(MessageTarget(SCOPE, "old"), "correction")).message
    successor = Mailbox(memory, session="new", clock=clock)
    assert await successor.list() == ()
    await note_continuation(successor, SCOPE, "old")
    assert len(await successor.list()) == 1
    # A cycle is tolerated, never an infinite traversal.
    await note_continuation(Mailbox(memory, session="old"), SCOPE, "new")
    assert await successor.lineage(SCOPE) == {"old", "new"}
    stranger = Mailbox(memory, session="stranger", clock=clock)
    assert (await stranger.view(SCOPE, item.id)).message.body == "correction"
    with pytest.raises(ValueError, match="not a recipient"):
        await stranger.update(SCOPE, item.id, "ack", occurrence=1)
    await sender.update(SCOPE, item.id, "cancel", occurrence=1)
    assert await successor.list() == ()


async def test_user_scope_and_mount_isolation(
    memory: MemoryConfig, clock: Clock
) -> None:
    first = Mailbox(memory, session="a", clock=clock)
    user = (await first.send(MessageTarget("user:test"), "shared reminder")).message
    await first.send(MessageTarget(SCOPE), "private project")
    second = Mailbox(
        MemoryConfig(
            memory.store,
            (Mount("user:test", "user"), Mount("user:test/proj:other", "project")),
        ),
        session="b",
        clock=clock,
    )
    assert [r.message.id for r in await second.list()] == [user.id]
    with pytest.raises(ValueError, match="not mounted"):
        await second.view(SCOPE, user.id)


async def test_redaction_erases_message_and_history(mailbox: Mailbox) -> None:
    item = (await mailbox.send(MessageTarget(SCOPE), "erase me")).message
    await mailbox.update(SCOPE, item.id, "ack", occurrence=1)
    await mailbox.memory.store.redact(SCOPE, path=message_path(item.id))
    assert await mailbox.all() == ()
    with pytest.raises(MemoryDocumentNotFoundError):
        await mailbox.history(SCOPE, item.id)


async def test_validation_and_release(mailbox: Mailbox, clock: Clock) -> None:
    with pytest.raises(ValueError):
        MessageTarget(SCOPE, turn=1)
    with pytest.raises(ValueError):
        MailboxConfig(poll_seconds=0)
    with pytest.raises(ValueError):
        await mailbox.send(MessageTarget(SCOPE), "")
    with pytest.raises(ValueError):
        await mailbox.send(MessageTarget(SCOPE), "bad", due_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        await mailbox.send(MessageTarget(SCOPE), "bad", delay_seconds=-1)
    with pytest.raises(ValueError):
        await mailbox.send(
            MessageTarget(SCOPE), "bad", due_at=clock.now(), delay_seconds=1
        )
    item = (await mailbox.send(MessageTarget(SCOPE), "work", actionable=True)).message
    claimed = await mailbox.update(SCOPE, item.id, "claim", occurrence=1)
    with pytest.raises(ValueError, match="outcome"):
        await mailbox.update(
            SCOPE, item.id, "complete", occurrence=1, token=claimed.message.claim_token
        )
    await mailbox.update(
        SCOPE, item.id, "release", occurrence=1, token=claimed.message.claim_token
    )
    assert len(await mailbox.list()) == 1
    assert len(await context(mailbox, budget=600)) <= 600
