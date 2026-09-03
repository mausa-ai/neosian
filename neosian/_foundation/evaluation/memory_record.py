"""The cross-client cell: a foreign agent's session, replayed (§21.7).

A `record` session is what a client's hooks would have sent `neosian
record` — the prompt, the tool rounds, the stop — driven through the
verb's own engine against the cell's store root, so the turn carries
the foreign actor and the scope gains its sessions document exactly as
in production. The reading session then starts the way a hook-fed
agent starts: the memory section plus "where we left off" in its
prefix, and the server's `recall_turn` (conversation required) beside
its memory tool. That is the measured switching claim: one client
writes, a different one recalls — never asserted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.recall import create_recall_any_tool
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.settings import DEFAULT_SCHEMA, StoreSettings
from neosian._foundation.record.cli import record_payload
from neosian._foundation.record.context import render_left_off
from neosian._foundation.record.settings import RecordSettings

if TYPE_CHECKING:
    from pathlib import Path

    from neosian._foundation.evaluation.memory_types import RecordedSession
    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.shared.types import ToolFunction

_REPLAY_ACTOR = "cli:record"  # StoreSettings needs one; the session's is stamped


def payloads_of(session: RecordedSession) -> list[dict[str, Any]]:
    """The hook payloads the session stands for, in hook order."""
    common = {"session_id": session.session_id}
    rounds: list[dict[str, Any]] = []
    for tool in session.tools:
        payload: dict[str, Any] = {
            **common,
            "hook_event_name": "PostToolUse",
            "tool_name": tool.name,
            "tool_input": dict(tool.input),
            "tool_response": tool.response,
        }
        if tool.id is not None:
            payload["tool_use_id"] = tool.id
        rounds.append(payload)
    return [
        {**common, "hook_event_name": "UserPromptSubmit", "prompt": session.prompt},
        *rounds,
        {**common, "hook_event_name": "Stop", "last_assistant_message": session.stop},
    ]


def first_writable(mounts: tuple[Mount, ...]) -> Mount:
    """Where the sessions document lands — the verb's own rule."""
    for mount in mounts:
        if not mount.read_only and not mount.edit_only:
            return mount
    raise RuntimeError("a record session needs a read-write mount")


async def replay_record(
    session: RecordedSession, *, store_root: Path, mounts: tuple[Mount, ...]
) -> None:
    """Land the session through the verb's engine on the cell's root.

    Every transport's backing is this FileStore (the http cell's state
    process serves it), and a hook is its own process writing there —
    so the replay is the local store, never the reading session's
    handle. A span that does not land is a harness error.
    """
    settings = RecordSettings(
        store=StoreSettings(
            mounts=mounts,
            root=store_root,
            dsn=None,
            schema=DEFAULT_SCHEMA,
            actor=_REPLAY_ACTOR,
        ),
        mount=first_writable(mounts),
        agent=session.agent,
        spool=store_root / ".spool",
    )
    for payload in payloads_of(session):
        envelope = await record_payload(settings, json.dumps(payload))
    if envelope["disposition"] != "recorded":
        raise RuntimeError(
            f"record session {session.session_id!r} did not land: "
            f"{envelope['disposition']}"
        )


def _conversations(config: MemoryConfig) -> ConversationStore:
    if not isinstance(config.store, ConversationStore):
        raise RuntimeError("session_start needs a store that keeps conversations")
    return config.store


async def session_start_section(config: MemoryConfig, *, own: str) -> str:
    """The reading session's prefix: the memory section, then the
    hook's "where we left off" over the scope's sessions documents."""
    left_off = await render_left_off(
        config.store,
        _conversations(config),
        first_writable(config.mounts).scope,
        own=own,
        source="startup",
    )
    return f"{await memory_system_section(config)}\n\n{left_off}"


def recall_any_tool(config: MemoryConfig) -> ToolFunction:
    """The server's `recall_turn` over the cell's own store handle — on
    the http transport the recall crosses the wire."""
    return create_recall_any_tool(_conversations(config))
