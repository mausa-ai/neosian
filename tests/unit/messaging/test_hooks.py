"""Hook delivery stays session-bound and never acknowledges rendering."""

import io
import json
from pathlib import Path
from typing import Any

from neosian import FileStore, MemoryConfig
from neosian._foundation.memory.settings import StoreSettings
from neosian._foundation.record.cli import record_payload, run
from neosian._foundation.record.settings import RecordSettings
from neosian.messaging import Mailbox, MessageTarget

from .conftest import SCOPE


def settings(memory: MemoryConfig, tmp_path: Path) -> RecordSettings:
    return RecordSettings(
        StoreSettings(memory.mounts, tmp_path / "store", None, "neosian", "test:hook"),
        memory.mounts[0],
        "claude-code",
        tmp_path / "spool",
    )


async def test_prompt_delivery_repeats_then_stops(
    memory: MemoryConfig, tmp_path: Path
) -> None:
    box = Mailbox(memory, session="live")
    item = (
        await box.send(MessageTarget(SCOPE, "live"), "review the correction")
    ).message
    args = [
        "--root",
        str(tmp_path / "store"),
        "--scope",
        SCOPE,
        "--spool",
        str(tmp_path / "spool"),
    ]
    for event in ("UserPromptSubmit", "PostToolUse"):
        out, err = io.StringIO(), io.StringIO()
        assert (
            await run(
                args,
                {},
                stdin=io.StringIO(
                    json.dumps(
                        {
                            "session_id": "live",
                            "hook_event_name": event,
                            "prompt": "go",
                        }
                    )
                ),
                out=out,
                err=err,
            )
            == 0
        ), err.getvalue()
        frame = json.loads(out.getvalue())["hookSpecificOutput"]
        assert frame["hookEventName"] == event
        assert item.id in frame["additionalContext"]
    assert (await box.view(SCOPE, item.id)).version == 1
    await box.update(SCOPE, item.id, "ack", occurrence=1)
    result = await record_payload(
        settings(memory, tmp_path),
        json.dumps(
            {
                "session_id": "live",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "go",
            }
        ),
    )
    assert result["context"] is None


async def test_hook_reservation_and_received_context(
    memory: MemoryConfig, tmp_path: Path
) -> None:
    box = Mailbox(memory, session="live")
    item = (await box.send(MessageTarget(SCOPE), "wake now", delivery="wake")).message
    config = settings(memory, tmp_path)

    async def event(name: str, **values: Any) -> dict[str, Any]:
        return await record_payload(
            config,
            json.dumps(
                {
                    "session_id": "live",
                    "hook_event_name": name,
                    **values,
                }
            ),
        )

    reserved = (await event("MailboxPoll"))["messages"][0]
    assert reserved["id"] == item.id
    assert (await event("MailboxPoll"))["messages"] == []
    await event(
        "MailboxDelivery",
        scope=SCOPE,
        message_id=item.id,
        occurrence=1,
        token=reserved["token"],
        accepted=False,
    )
    reserved = (await event("MailboxPoll"))["messages"][0]
    await event(
        "MailboxDelivery",
        scope=SCOPE,
        message_id=item.id,
        occurrence=1,
        token=reserved["token"],
        accepted=True,
    )
    assert (await event("MailboxPoll"))["messages"] == []
    assert "wake now" in (await event("MailboxContext"))["context"]
    await event("MailboxReceived", text=reserved["text"])
    await event("Stop", last_assistant_message="checking")
    assert isinstance(memory.store, FileStore)
    turn = (await memory.store.read_turns("live"))[0]
    assert "[neosian delivery" in str(turn.messages[0].content)
    await box.update(SCOPE, item.id, "ack", occurrence=1)
    identity = (await event("MailboxContext"))["context"]
    assert "session identity: live" in identity and item.id not in identity


async def test_tool_continuation_routes_before_stop(
    memory: MemoryConfig, tmp_path: Path
) -> None:
    box = Mailbox(memory, session="sender")
    await box.send(MessageTarget(SCOPE, "past"), "later correction")
    result = await record_payload(
        settings(memory, tmp_path),
        json.dumps(
            {
                "session_id": "successor",
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__neosian_memory__continue_session",
                "tool_response": "[continuing conversation past]",
            }
        ),
    )
    assert "later correction" in result["context"]
    assert len(await Mailbox(memory, session="successor").list()) == 1
