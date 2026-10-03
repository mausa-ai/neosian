"""Mailbox records survive portability and stay outside memory gardening."""

import io
import json
from pathlib import Path

from neosian import FileStore, MemoryConfig
from neosian._foundation.conversation.cli_search import run
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.index import generate_memory_index
from neosian._foundation.memory.maintenance import protection_reason
from neosian._foundation.memory.payload import render_documents
from neosian._foundation.memory.transfer import transfer
from neosian._foundation.messaging.types import message_path
from neosian.messaging import Mailbox, MessageTarget

from .conftest import SCOPE, Clock


async def test_export_import_and_protection(
    memory: MemoryConfig, mailbox: Mailbox, clock: Clock, tmp_path: Path
) -> None:
    item = (
        await mailbox.send(MessageTarget(SCOPE), "not a memory fact", actionable=True)
    ).message
    claimed = await mailbox.update(SCOPE, item.id, "claim", occurrence=1)
    await mailbox.update(
        SCOPE,
        item.id,
        "snooze",
        occurrence=1,
        token=claimed.message.claim_token,
        delay_seconds=1209600,
        outcome="the first finding",
    )
    path = "/project/" + message_path(item.id)
    for command, arguments in (
        ("create", {"path": path, "content": "corrupt"}),
        ("delete", {"path": path}),
        ("rename", {"old_path": path, "new_path": "/project/elsewhere"}),
    ):
        assert not (await dispatch(memory, command, arguments)).success
    assert await protection_reason(memory, path, clock.now(), deletion=False)
    payload = await render_documents(memory, fence="test", edit_only_note="fixed")
    assert item.id not in payload and item.body not in payload
    index = await generate_memory_index(memory.store, memory.mounts)
    assert "1 mailbox records" in index and item.id not in index
    archive = FileStore(tmp_path / "archive")
    restored = FileStore(tmp_path / "restored")
    await transfer(memory.store, archive)
    await transfer(archive, restored)
    box = Mailbox(
        MemoryConfig(restored, memory.mounts), session="restored", clock=clock
    )
    assert (await box.view(SCOPE, item.id)).message.occurrence == 2
    assert any(
        row.outcome == "the first finding" for row in await box.history(SCOPE, item.id)
    )
    assert await box.list() == ()
    clock.advance(1209600)
    assert len(await box.list()) == 1


async def test_cli_search_finds_prior_outcomes(
    mailbox: Mailbox, clock: Clock, tmp_path: Path
) -> None:
    item = (
        await mailbox.send(MessageTarget(SCOPE), "upstream status", actionable=True)
    ).message
    for finding in ("cobalt fix absent", "newer finding"):
        current = (await mailbox.view(SCOPE, item.id)).message
        claim = await mailbox.update(
            SCOPE, item.id, "claim", occurrence=current.occurrence
        )
        await mailbox.update(
            SCOPE,
            item.id,
            "snooze",
            occurrence=current.occurrence,
            token=claim.message.claim_token,
            delay_seconds=1,
            outcome=finding,
        )
        clock.advance(1)
    out, err = io.StringIO(), io.StringIO()
    assert (
        await run(
            ["cobalt", "--root", str(tmp_path / "store"), "--json"],
            {},
            out=out,
            err=err,
        )
        == 0
    ), err.getvalue()
    (hit,) = json.loads(out.getvalue())["hits"]
    assert hit["type"] == "message" and hit["message_id"] == item.id
    assert "cobalt" in hit["snippet"]
