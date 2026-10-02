"""The read side of `neosian record`: `SessionStart` (DESIGN §21.7, §33).

Claude Code, Codex, Cursor and Muse add a SessionStart hook's output to
the model's context, so the verb answers with the memory index, the
pending handoff note and "where we left off": the scope's recent
sessions, log-projected the way a view is (§21.3) — one line per turn,
the turn number in brackets — so `recall_turn(n, conversation=…)` on
the MCP server re-reads any of them verbatim. Never a write, never the
spool. `source: compact` is the re-injection after the client paged its
own history, so the own session's record is what comes back, and no
note; every other source gets the most recently written sessions in the
scope, newest first. One budget per client at the output edge
(`CONTEXT_CHARS`, what the client injects whole), shared by the three
blocks, so a busy scope never trips a client into a file-path preview.
A client with no start hook gets the same blocks in the MCP server's
instructions (`render_instructions`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.links import LinkRegistry
from neosian._foundation.conversation.views import project_conversation
from neosian._foundation.memory.index import (
    INDEX_BUDGET_CHARS,
    generate_memory_index,
    memory_system_section,
)
from neosian._foundation.memory.sessions import (
    HANDOFF_PATH,
    SESSIONS_PREFIX,
    handoff_tier,
    parse_handoff,
    sessions_mount,
)
from neosian._foundation.shared.prompt_assets import get_prompt, render

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.mounts import MemoryConfig
    from neosian._foundation.memory.types import MemoryEntry
    from neosian._foundation.record.settings import RecordSettings

SESSION_START_EVENT: Final = "SessionStart"
COMPACT_SOURCE: Final = "compact"
RECENT_SESSIONS: Final = 3
LEFT_OFF_BUDGET_CHARS: Final = INDEX_BUDGET_CHARS
# What each client injects whole from a SessionStart hook (verified
# 2026-10-03, §33): Claude Code's documented 10,000 chars; Codex's 2,500
# tokens at the §6 heuristic with margin; Cursor's undocumented cap at the
# conservative figure; Muse's 16 KiB, cut again in bytes at the edge.
CONTEXT_CHARS: Final = {
    "claude-code": 10_000,
    "codex": 9_000,
    "cursor": 10_000,
    "muse-code": 16_382,
}
DEFAULT_CONTEXT_CHARS: Final = 10_000


def choose_sessions(
    entries: Sequence[MemoryEntry],
    *,
    own: str,
    source: str,
    limit: int = RECENT_SESSIONS,
) -> list[str]:
    """The conversations to project, newest first: the own session alone
    after a compaction (when it is recorded), else the `limit` most
    recently written sessions documents — a redacted one never."""
    listed = sorted(
        (e for e in entries if e.path.startswith(SESSIONS_PREFIX) and not e.redacted),
        key=lambda e: e.updated_at,
        reverse=True,
    )
    ids = [e.path[len(SESSIONS_PREFIX) :] for e in listed]
    if source == COMPACT_SOURCE and own in ids:
        return [own]
    return ids[:limit]


async def render_left_off(
    memory: MemoryStore,
    conversations: ConversationStore,
    scope: str,
    *,
    own: str,
    source: str,
    budget_chars: int = LEFT_OFF_BUDGET_CHARS,
) -> str:
    """The "where we left off" block, at most `budget_chars` long: every
    chosen session as log lines under an even share of what the frame
    lines leave (the fold line says what is hidden), framed so the model
    knows the door and the recall call."""
    entries = await memory.list_documents(scope, prefix=SESSIONS_PREFIX)
    header, footer = get_prompt("context.start_header"), get_prompt(
        "context.start_footer"
    )
    sessions = [
        (conversation_id, turns)
        for conversation_id in choose_sessions(entries, own=own, source=source)
        if (turns := await conversations.read_turns(conversation_id))
    ]
    heads = [
        render(
            get_prompt("context.start_session"),
            conversation_id=conversation_id,
            actor=turns[-1].actor or "-",
        )
        for conversation_id, turns in sessions
    ]
    frames = len(header) + len(footer) + sum(len(head) + 1 for head in heads) + 2
    share = max((budget_chars - frames) // max(len(sessions), 1), 1)
    widths = CompactionConfig()
    blocks: list[str] = []
    for head, (conversation_id, turns) in zip(heads, sessions, strict=True):
        lines = project_conversation(
            turns,
            await conversations.read_projections(conversation_id),
            digest_chars=widths.digest_chars,
            user_chars=widths.user_chars,
            budget_chars=share,
            links=LinkRegistry.of(turns, source=conversation_id),
        )
        blocks.append("\n".join([head, *lines]))
    body = "\n".join(blocks) if blocks else get_prompt("context.start_empty")
    return "\n".join([header, body, footer])


async def render_note(
    memory: MemoryStore, scope: str, *, mount_path: str, now: datetime
) -> str | None:
    """The pending handoff note as session start shows it (§33): in full
    while young, one line once stale, nothing once picked up or absent."""
    document = await memory.read(scope, HANDOFF_PATH)
    if document is None or document.redacted:
        return None
    baton = parse_handoff(document.content)
    tier = None if baton is None else handoff_tier(baton, now)
    if baton is None or tier is None:
        return None
    frame = {
        "actor": baton.actor,
        "written": baton.written.isoformat(timespec="minutes").replace("+00:00", "Z"),
        "argument": (
            "" if baton.conversation is None else f'conversation="{baton.conversation}"'
        ),
    }
    if tier == "line":
        return render(get_prompt("context.start_note_line"), mount=mount_path, **frame)
    head = render(get_prompt("context.start_note"), **frame)
    return "\n".join([head, baton.note, get_prompt("context.start_note_end")])


async def render_start_context(
    memory: MemoryStore,
    conversations: ConversationStore,
    scope: str,
    *,
    mount_path: str,
    own: str,
    source: str,
    now: datetime,
    budget_chars: int = LEFT_OFF_BUDGET_CHARS,
) -> str:
    """The note (never after a compaction), then "where we left off" under
    what the budget leaves."""
    note = (
        None
        if source == COMPACT_SOURCE
        else await render_note(memory, scope, mount_path=mount_path, now=now)
    )
    remaining = budget_chars if note is None else budget_chars - len(note) - 2
    left_off = await render_left_off(
        memory,
        conversations,
        scope,
        own=own,
        source=source,
        budget_chars=max(remaining, 1),
    )
    return left_off if note is None else f"{note}\n\n{left_off}"


async def render_session_start(
    memory: MemoryStore,
    conversations: ConversationStore,
    settings: RecordSettings,
    *,
    session_id: str,
    source: str,
    now: datetime,
) -> str:
    """What the hook prints under the client's ceiling: the index up to
    three eighths of it, the note, where we left off under the rest."""
    assert settings.mount is not None  # the verb's layout always yields one
    budget = CONTEXT_CHARS.get(settings.agent, DEFAULT_CONTEXT_CHARS)
    index = await generate_memory_index(
        memory, settings.store.mounts, budget_chars=budget * 3 // 8
    )
    head = f"{get_prompt('context.start_index')}\n{index}"
    rest = await render_start_context(
        memory,
        conversations,
        settings.mount.scope,
        mount_path=settings.mount.mount_path,
        own=session_id,
        source=source,
        now=now,
        budget_chars=max(budget - len(head) - 2, 1),
    )
    return f"{head}\n\n{rest}"


async def render_instructions(
    config: MemoryConfig,
    conversations: ConversationStore | None,
    *,
    now: datetime,
    own: str = "",
) -> str:
    """The start blocks for a door with no hook (§33): the memory section
    with its index, then the note and where we left off at the full
    budgets — the MCP server's instructions, where the session is unknown
    and none is the own one; the harness's prefix, where it is known."""
    section = await memory_system_section(config)
    mount = sessions_mount(config.mounts)
    if conversations is None or mount is None:
        return section
    rest = await render_start_context(
        config.store,
        conversations,
        mount.scope,
        mount_path=mount.mount_path,
        own=own,
        source="startup",
        now=now,
    )
    return f"{section}\n\n{rest}"
