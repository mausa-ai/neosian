"""The same mailbox on tools, CLI, native conversations and remote storage."""

import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest

from neosian import (
    AgentConfig,
    Conversation,
    FileStore,
    MemoryConfig,
    Model,
    Mount,
    ReflectionConfig,
    RemoteStore,
)
from neosian._foundation.conversation.search_history import history_any_tools
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.messaging.cli import run
from neosian._foundation.messaging.history import augment_history
from neosian._foundation.server.app import build_app
from neosian._foundation.tools.base import get_tool_definition
from neosian.fake import FakeClient, FakeScript, FakeTurn
from neosian.mcp import McpServer, create_memory_server
from neosian.messaging import (
    InboxMessage,
    Mailbox,
    MailboxConfig,
    MessageTarget,
    create_messages_tool,
    listen,
    receive_once,
)

from .conftest import SCOPE, Clock


async def test_shared_tool_and_cli(
    memory: MemoryConfig, mailbox: Mailbox, tmp_path: Path
) -> None:
    tool = create_messages_tool(memory, session="recipient")
    sent = await tool(command="send", scope="/project", body="tool message")
    assert sent.success and sent.data is not None
    item = json.loads(sent.data)
    assert (
        await tool(command="ack", scope="/project", message_id=item["id"], occurrence=1)
    ).success
    assert not (
        await tool(
            command="ack",
            scope="/project",
            message_id=item["id"],
            session="stranger",
            occurrence=1,
        )
    ).success
    assert (await tool(command="view", scope="/project", message_id=item["id"])).success
    assert not (await tool(command="send", body="missing scope")).success
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        [
            "send",
            "--root",
            str(tmp_path / "store"),
            "--scope",
            SCOPE,
            "--body",
            "CLI message",
            "--json",
        ],
        {},
        out=out,
        err=err,
    )
    assert code == 0, err.getvalue()
    assert any(r.message.body == "CLI message" for r in await mailbox.all())
    assert json.loads(out.getvalue())["success"]


async def test_wake_reservation_no_loop_and_retry(
    memory: MemoryConfig, clock: Clock
) -> None:
    left = Mailbox(memory, session="left", clock=clock)
    right = Mailbox(memory, session="right", clock=clock)
    await left.send(MessageTarget(SCOPE), "wake", delivery="wake")
    seen: list[str] = []

    async def deliver(item: InboxMessage) -> bool:
        seen.append(item.id)
        await asyncio.sleep(0)
        return True

    await asyncio.gather(receive_once(left, deliver), receive_once(right, deliver))
    assert len(seen) == 1
    await receive_once(left, deliver)
    assert len(seen) == 1
    assert len(await left.list()) == 1  # accepted is not read
    second = (await left.send(MessageTarget(SCOPE), "retry", delivery="wake")).message

    async def refuse(_item: InboxMessage) -> bool:
        return False

    assert await receive_once(left, refuse) == ()
    assert (await left.view(SCOPE, second.id)).message.wake_token is None
    await receive_once(right, deliver)
    assert len(seen) == 2
    task = asyncio.create_task(listen(left, deliver))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_lost_receiver_reservation_expires(
    mailbox: Mailbox, clock: Clock
) -> None:
    item = (await mailbox.send(MessageTarget(SCOPE), "wake", delivery="wake")).message
    first = await mailbox.update(SCOPE, item.id, "reserve", occurrence=1)
    clock.advance(61)
    next_ = await mailbox.update(SCOPE, item.id, "reserve", occurrence=1)
    assert next_.message.wake_token != first.message.wake_token
    with pytest.raises(ValueError, match="reservation"):
        await mailbox.update(
            SCOPE, item.id, "accepted", occurrence=1, token=first.message.wake_token
        )


async def test_unread_overflow_does_not_starve_wake(mailbox: Mailbox) -> None:
    for n in range(51):
        await mailbox.send(MessageTarget(SCOPE), f"ordinary unread {n}")
    item = (
        await mailbox.send(
            MessageTarget(SCOPE), "wake behind overflow", delivery="wake"
        )
    ).message
    seen = []

    async def deliver(message: InboxMessage) -> bool:
        seen.append(message.id)
        return True

    await receive_once(mailbox, deliver)
    assert seen == [item.id]


async def test_history_finds_corrections_and_preserves_original(
    memory: MemoryConfig, mailbox: Mailbox
) -> None:
    assert isinstance(memory.store, FileStore)
    await memory.store.append_turn(
        "old", [Message(role=Role.USER, content="the old assumption")]
    )
    item = (
        await mailbox.send(
            MessageTarget(SCOPE, "old", 1), "correction: cobalt is supported"
        )
    ).message
    recall, search = [
        augment_history(t, memory.store, mailbox)
        for t in history_any_tools(memory.store)
    ]
    hit = await search(query="cobalt", conversation="old")
    assert hit.success and item.id in str(hit.data)
    original = await search(query="old assumption")
    assert "correction" in str(original.data)
    full = await recall(turn=1, conversation="old")
    assert "the old assumption" in str(full.data) and "later annotations" in str(
        full.data
    )
    assert len(await memory.store.read_turns("old")) == 1


async def test_native_context_and_streaming(memory: MemoryConfig) -> None:
    assert isinstance(memory.store, FileStore)
    box = Mailbox(memory, session="writer")
    await box.send(MessageTarget(SCOPE, "native"), "migration ready")
    fake = FakeClient(
        FakeScript(turns=(FakeTurn(content="read"), FakeTurn(content="again")))
    )
    config = AgentConfig(
        system_prompt="Test.", model=Model.FAKE, client_factory=lambda _: fake
    )
    convo = Conversation(
        config,
        store=memory.store,
        conversation_id="native",
        memory=memory,
        mailbox=MailboxConfig(),
        reflection=ReflectionConfig(enabled=False),
    )
    async with convo:
        await convo.send("continue")
        events = await convo.send("next", stream=True)
        async for _ in events:
            pass
    turns = await memory.store.read_turns("native")
    assert len(turns) == 2
    assert "migration ready" in str(turns[0].messages[0].content)
    assert "migration ready" in str(turns[1].messages[0].content)


async def test_remote_store_and_redaction(tmp_path: Path) -> None:
    backend = FileStore(tmp_path / "remote")
    app = await build_app(backend, token="unit-test")
    store = await RemoteStore.connect(
        "http://test", token="unit-test", transport=httpx.ASGITransport(app=app)
    )
    try:
        box = Mailbox(MemoryConfig(store, (Mount(SCOPE, "project"),)), session="remote")
        item = (
            await box.send(MessageTarget(SCOPE), "remote work", actionable=True)
        ).message
        claim = await box.update(SCOPE, item.id, "claim", occurrence=1)
        await box.update(
            SCOPE,
            item.id,
            "snooze",
            occurrence=1,
            token=claim.message.claim_token,
            delay_seconds=1209600,
            outcome="not yet",
        )
        assert len(await box.history(SCOPE, item.id)) == 3
        await store.redact(SCOPE)
        assert await box.all() == ()
    finally:
        await store.aclose()


async def test_messages_over_real_mcp_session(memory: MemoryConfig) -> None:
    server = await create_memory_server(memory)
    async with McpServer.in_process(server) as client:
        tool = next(
            t
            for t in client.tools
            if (d := get_tool_definition(t)) and d.name == "messages"
        )
        result = await tool(
            command="send",
            scope="/project",
            session="source",
            body="MCP check",
            actionable=True,
        )
        assert result.success and result.data is not None
        item = json.loads(result.data)
        claimed = await tool(
            command="claim",
            scope="/project",
            session="worker",
            message_id=item["id"],
            occurrence=1,
        )
        assert claimed.success and claimed.data is not None
        token = json.loads(claimed.data)["claim_token"]
        snoozed = await tool(
            command="snooze",
            scope="/project",
            session="worker",
            message_id=item["id"],
            occurrence=1,
            token=token,
            delay_seconds=1209600,
            outcome="no release yet",
        )
        assert snoozed.success and snoozed.data is not None
        assert json.loads(snoozed.data)["occurrence"] == 2
