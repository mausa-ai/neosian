"""Mailbox CAS works across independent database pools without new schema."""

import asyncio

from neosian import MemoryConfig, Mount, PostgresStore
from neosian.messaging import Mailbox, MessageTarget
from tests.support.postgres import store_schema


async def test_mailbox_claim_and_reset_across_pools(
    store: PostgresStore, postgres_dsn: str
) -> None:
    scope = "user:mailbox/proj:workers"
    mounts = (Mount(scope, "project"),)
    async with PostgresStore(postgres_dsn, schema=store_schema(store)) as other:
        left = Mailbox(MemoryConfig(store, mounts), session="left")
        right = Mailbox(MemoryConfig(other, mounts), session="right")
        item = (
            await left.send(MessageTarget(scope), "check once", actionable=True)
        ).message
        results = await asyncio.gather(
            left.update(scope, item.id, "claim", occurrence=1),
            right.update(scope, item.id, "claim", occurrence=1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ValueError) for result in results) == 1
        current = (await left.view(scope, item.id)).message
        winner = left if current.owner == "left" else right
        reset = await winner.update(
            scope,
            item.id,
            "snooze",
            occurrence=1,
            token=current.claim_token,
            delay_seconds=1209600,
            outcome="still no fix",
        )
        assert reset.message.occurrence == 2
        assert (await right.view(scope, item.id)).message == reset.message
        assert len(await left.history(scope, item.id)) == 3
