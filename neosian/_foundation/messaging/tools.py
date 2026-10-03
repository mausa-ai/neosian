"""One command service for the Python tool, MCP and CLI."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal, cast

from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.messaging.core import ListStatus, Mailbox
from neosian._foundation.messaging.types import (
    Action,
    DeliveryMode,
    MailboxConfig,
    MessageReceipt,
    MessageTarget,
)
from neosian._foundation.shared.clock import Clock
from neosian._foundation.shared.exceptions import NeosianError
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

Command = Literal[
    "send",
    "list",
    "view",
    "ack",
    "claim",
    "renew",
    "release",
    "complete",
    "snooze",
    "cancel",
]
COMMANDS = (
    "send",
    "list",
    "view",
    "ack",
    "claim",
    "renew",
    "release",
    "complete",
    "snooze",
    "cancel",
)


def receipt_json(receipt: MessageReceipt) -> dict[str, object]:
    return {"version": receipt.version, **receipt.message.model_dump(mode="json")}


def create_messages_tool(
    memory: MemoryConfig,
    *,
    session: str | None = None,
    actor: str | Callable[[], str] = "mcp:stdio",
    config: MailboxConfig | None = None,
    clock: Clock | None = None,
) -> ToolFunction:
    """Serve the same mailbox operations on every transport."""

    @Tool(
        name="messages",
        description=get_prompt("messages.tool"),
        params=get_prompt_params("messages.params"),
    )
    async def messages(
        command: Command,
        scope: str | None = None,
        session: str | None = None,
        message_id: str | None = None,
        body: str | None = None,
        conversation: str | None = None,
        turn: int | None = None,
        delivery: DeliveryMode = "next_activity",
        actionable: bool = False,
        due_at: str | None = None,
        delay_seconds: int | None = None,
        about: str | None = None,
        about_turn: int | None = None,
        occurrence: int | None = None,
        token: str | None = None,
        outcome: str | None = None,
        status: ListStatus = "unread",
        limit: int = 50,
    ) -> ToolResult[str]:
        try:
            if command not in COMMANDS:
                raise ValueError(f"unknown messages command: {command}")
            if bound_session is not None and session not in (None, bound_session):
                raise ValueError("calling session differs from the bound conversation")
            mailbox = Mailbox(
                memory,
                session=bound_session or session,
                actor=actor,
                config=config,
                clock=clock,
            )
            selected = scope
            for mount in memory.mounts:
                if scope in (mount.mount_path, "/" + mount.mount_path):
                    selected = mount.scope
                    break
            if command == "list":
                return ToolResult.ok(
                    json.dumps(
                        {
                            "items": [
                                receipt_json(r)
                                for r in await mailbox.list(
                                    scope=selected, status=status, limit=limit
                                )
                            ]
                        }
                    )
                )
            if selected is None:
                raise ValueError("scope is required")
            moment = None if due_at is None else datetime.fromisoformat(due_at)
            if command == "send":
                result = await mailbox.send(
                    MessageTarget(selected, conversation, turn),
                    body or "",
                    delivery=delivery,
                    actionable=actionable,
                    due_at=moment,
                    delay_seconds=delay_seconds,
                    about=about,
                    about_turn=about_turn,
                )
            else:
                if message_id is None:
                    raise ValueError("message_id is required")
                if command == "view":
                    result = await mailbox.view(selected, message_id)
                    return ToolResult.ok(
                        json.dumps(
                            {
                                **receipt_json(result),
                                "history": [
                                    item.model_dump(mode="json")
                                    for item in await mailbox.history(
                                        selected, message_id
                                    )
                                ],
                            }
                        )
                    )
                if occurrence is None:
                    raise ValueError("occurrence is required; view the message first")
                result = await mailbox.update(
                    selected,
                    message_id,
                    cast(Action, command),
                    occurrence=occurrence,
                    token=token,
                    outcome=outcome,
                    due_at=moment,
                    delay_seconds=delay_seconds,
                )
            return ToolResult.ok(json.dumps(receipt_json(result)))
        except (NeosianError, ValueError) as exc:
            return ToolResult.fail(str(exc))

    bound_session = session
    return messages
