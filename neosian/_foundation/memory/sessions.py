"""The sessions documents and the handoff baton (DESIGN §20.9, §22.2,
§32, §33).

A session is listed as a memory document, never through a store method:
`sessions/<conversation_id>` in the mount at `/project` when one is
present. The record verb writes one for a foreign agent's session, a
`Conversation` with a project mount writes its own, and the next
agent's "where we left off", `recall_turn` and `search_history` read
the listing back. Since N6 a document also says which sessions the
session continues, and the mount holds one baton, `handoff`: the note a
departing agent writes for whoever comes next. Both declarations are
read back from the record itself: the tools' calls in a turn, paired
with the headers their results begin with, because the MCP server never
knows the session it serves and only the hooks do. Pure vocabulary; the
writers live beside their callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from neosian._foundation.llm.base import Message, Role, text_of
from neosian._foundation.memory.home import PROJECT_MOUNT_PATH
from neosian._foundation.shared.exceptions import MemoryConflictError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.mounts import MemoryConfig, Mount

PROMPT_LINE_CHARS: Final = 200
SESSIONS_DIR: Final = "sessions"
SESSIONS_PREFIX: Final = f"{SESSIONS_DIR}/"
HANDOFF_PATH: Final = "handoff"
HANDOFF_NOTE_CHARS: Final = 2048
HANDOFF_FULL_DAYS: Final = 7
CONTINUE_TOOL: Final = "continue_session"
HANDOFF_TOOL: Final = "handoff"
# The headers the two tools' results begin with; a client prefixes the
# tool's name (`mcp__neosian-memory__continue_session`) and may JSON-dump
# the result, so the header is searched, never anchored.
CONTINUE_HEADER: Final = re.compile(r"\[continuing conversation ([A-Za-z0-9_.-]+)")
HANDOFF_HEADER: Final = "[handoff note recorded"


def sessions_path(session_id: str) -> str:
    return f"{SESSIONS_DIR}/{session_id}"


def sessions_document(
    *,
    agent: str,
    session_id: str,
    started: datetime,
    last_prompt: str | None,
    turns: int,
    continues: Sequence[str] = (),
) -> str:
    """The scope's per-session document — the listing the next agent
    finds in its index (memory documents, never a store method)."""
    first_line = (last_prompt or "").strip().splitlines()
    prompt = first_line[0][:PROMPT_LINE_CHARS] if first_line else "-"
    return (
        f"# {agent} session {session_id}\n\n"
        f"- agent: {agent}\n"
        f"- conversation: {session_id}\n"
        f"- started: {_stamp(started)}\n"
        f"- last prompt: {prompt}\n"
        f"- turns: {turns}\n" + "".join(f"- continues: {c}\n" for c in continues)
    )


@dataclass(frozen=True, slots=True)
class SessionLines:
    """What a sessions document says, read tolerantly: an unknown line is
    ignored and a missing one is None."""

    agent: str | None
    conversation: str | None
    continues: tuple[str, ...]


def parse_sessions_document(content: str) -> SessionLines:
    pairs = _pairs(content)
    fields = dict(reversed(pairs))  # the first value of a repeated key
    return SessionLines(
        agent=fields.get("agent"),
        conversation=fields.get("conversation"),
        continues=tuple(value for key, value in pairs if key == "continues"),
    )


def continued_ids(messages: Sequence[Message]) -> tuple[str, ...]:
    """The conversations a turn declared it continues, in order: each
    `continue_session` call whose result carries the header the tool
    authored (the first header, since the delivered text may quote
    another); a stranger tool quoting one never counts."""
    results = _results(messages)
    found: dict[str, None] = {}
    for message in messages:
        for call in message.tool_calls:
            if _is_tool(str(call.name), CONTINUE_TOOL):
                match = CONTINUE_HEADER.search(results.get(call.id, ""))
                if match is not None:
                    found.setdefault(match.group(1))
    return tuple(found)


def handoff_declared(messages: Sequence[Message]) -> bool:
    """Whether the turn wrote the baton: a `handoff` call acknowledged."""
    results = _results(messages)
    return any(
        _is_tool(str(call.name), HANDOFF_TOOL)
        and HANDOFF_HEADER in results.get(call.id, "")
        for message in messages
        for call in message.tool_calls
    )


@dataclass(frozen=True, slots=True)
class Handoff:
    """The baton as a reader sees it: who wrote it and when, the note,
    the session it belongs to once the record linked it, and whether a
    session has picked it up."""

    actor: str
    written: datetime
    note: str
    conversation: str | None
    pending: bool


def handoff_document(
    *,
    actor: str,
    written: datetime,
    note: str,
    conversation: str | None = None,
    picked_up: tuple[datetime, str] | None = None,
) -> str:
    status = (
        "pending"
        if picked_up is None
        else f"picked up {_stamp(picked_up[0])} by {picked_up[1]}"
    )
    link = "" if conversation is None else f"- conversation: {conversation}\n"
    return (
        f"# handoff from {actor}\n\n"
        f"- from: {actor}\n- written: {_stamp(written)}\n- status: {status}\n"
        f"{link}\n{note.strip()}\n"
    )


def parse_handoff(content: str) -> Handoff | None:
    """The baton read back, or None when the document is not one (a hand
    edit): a reader shows nothing rather than guessing."""
    _, _, rest = content.partition("\n\n")
    head, _, note = rest.partition("\n\n")
    fields = dict(_pairs(head))
    try:
        written = datetime.fromisoformat(fields["written"].replace("Z", "+00:00"))
        actor, status = fields["from"], fields["status"]
    except (KeyError, ValueError):
        return None
    if written.tzinfo is None:
        return None
    return Handoff(
        actor=actor,
        written=written,
        note=note.strip(),
        conversation=fields.get("conversation"),
        pending=status == "pending",
    )


def handoff_tier(handoff: Handoff, now: datetime) -> Literal["full", "line"] | None:
    """How session start shows the baton: in full while pending and younger
    than `HANDOFF_FULL_DAYS`, one line while pending after that, not at all
    once picked up — a stale note never greets every session forever."""
    if not handoff.pending:
        return None
    young = now - handoff.written < timedelta(days=HANDOFF_FULL_DAYS)
    return "full" if young else "line"


def sessions_mount(mounts: Sequence[Mount]) -> Mount | None:
    """Where the record verb's sessions document lands (§22): the mount
    at `/project` when present, else the first read-write one; None when
    no mount can take it."""
    writable = [m for m in mounts if not m.read_only and not m.edit_only]
    for mount in writable:
        if mount.mount_path == PROJECT_MOUNT_PATH:
            return mount
    return writable[0] if writable else None


def project_mount(config: MemoryConfig | None) -> Mount | None:
    """The mount at `/project`, whatever its flags: the project whose
    sessions a Conversation lists and searches (§32)."""
    if config is None:
        return None
    for mount in config.mounts:
        if mount.mount_path == PROJECT_MOUNT_PATH:
            return mount
    return None


async def link_handoff(
    store: MemoryStore, scope: str, conversation: str, *, actor: str
) -> bool:
    """The record linking the pending, unlinked baton to the session whose
    span wrote it — the hooks know the session, the tool did not. Against
    the version read; a conflict means the pickup landed first and knows
    the target already, so nothing is written. True when it linked."""
    document = await store.read(scope, HANDOFF_PATH)
    if document is None or document.redacted:
        return False
    baton = parse_handoff(document.content)
    if baton is None or not baton.pending or baton.conversation is not None:
        return False
    content = handoff_document(
        actor=baton.actor,
        written=baton.written,
        note=baton.note,
        conversation=conversation,
    )
    try:
        await store.write(
            scope, HANDOFF_PATH, content, actor=actor, expected_version=document.version
        )
    except MemoryConflictError:
        return False
    return True


async def session_ids(store: MemoryStore, scope: str) -> tuple[str, ...]:
    """The conversation ids `scope` lists under `sessions/`, in path
    order: a redacted document is not listed, nor a nested path (one
    segment is a conversation id by construction — the two grammars are
    the same)."""
    entries = await store.list_documents(scope, prefix=SESSIONS_PREFIX)
    ids = (
        entry.path[len(SESSIONS_PREFIX) :] for entry in entries if not entry.redacted
    )
    return tuple(name for name in ids if name and "/" not in name)


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _pairs(text: str) -> list[tuple[str, str]]:
    """The `- key: value` lines of a document, in order."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith("- ") and ": " in line:
            key, _, value = line[2:].partition(": ")
            pairs.append((key, value.strip()))
    return pairs


def _results(messages: Sequence[Message]) -> dict[str | None, str]:
    return {m.tool_call_id: text_of(m) for m in messages if m.role is Role.TOOL}


def _is_tool(name: str, tool: str) -> bool:
    return name == tool or name.endswith(f"_{tool}")
