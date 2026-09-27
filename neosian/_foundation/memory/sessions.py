"""The sessions documents (DESIGN §20.9, §22.2, §32).

A session is listed as a memory document, never through a store method:
`sessions/<conversation_id>` in the mount at `/project` when one is
present. The record verb writes one for a foreign agent's session, a
`Conversation` with a project mount writes its own, and the next
agent's "where we left off", `recall_turn` and `search_history` read
the listing back. Pure vocabulary; the writers live beside their
callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.memory.home import PROJECT_MOUNT_PATH

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.mounts import MemoryConfig, Mount

PROMPT_LINE_CHARS: Final = 200
SESSIONS_DIR: Final = "sessions"
SESSIONS_PREFIX: Final = f"{SESSIONS_DIR}/"


def sessions_path(session_id: str) -> str:
    return f"{SESSIONS_DIR}/{session_id}"


def sessions_document(
    *,
    agent: str,
    session_id: str,
    started: datetime,
    last_prompt: str | None,
    turns: int,
) -> str:
    """The scope's per-session document — the listing the next agent
    finds in its index (memory documents, never a store method)."""
    first_line = (last_prompt or "").strip().splitlines()
    prompt = first_line[0][:PROMPT_LINE_CHARS] if first_line else "-"
    stamp = started.isoformat().replace("+00:00", "Z")
    return (
        f"# {agent} session {session_id}\n\n"
        f"- agent: {agent}\n"
        f"- conversation: {session_id}\n"
        f"- started: {stamp}\n"
        f"- last prompt: {prompt}\n"
        f"- turns: {turns}\n"
    )


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
