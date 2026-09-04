"""The read side of `neosian record`: `SessionStart` (DESIGN §21.7).

Claude Code and Codex add a SessionStart hook's stdout to the model's
context, so the verb answers with the memory index and "where we left
off": the scope's recent sessions, log-projected the way a view is
(§21.3) — one line per turn, the turn number in brackets — so
`recall_turn(n, conversation=…)` on the MCP server re-reads any of them
verbatim. Never a write, never the spool. `source: compact` is the
re-injection after the client paged its own history, so the own
session's record is what comes back; every other source gets the most
recently written sessions in the scope, newest first, under one budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.links import LinkRegistry
from neosian._foundation.conversation.views import project_conversation
from neosian._foundation.memory.index import INDEX_BUDGET_CHARS, generate_memory_index
from neosian._foundation.record.span import SESSIONS_DIR
from neosian._foundation.shared.prompt_assets import get_prompt, render

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.types import MemoryEntry
    from neosian._foundation.record.settings import RecordSettings

SESSION_START_EVENT: Final = "SessionStart"
COMPACT_SOURCE: Final = "compact"
RECENT_SESSIONS: Final = 3
LEFT_OFF_BUDGET_CHARS: Final = INDEX_BUDGET_CHARS
_PREFIX: Final = f"{SESSIONS_DIR}/"


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
        (e for e in entries if e.path.startswith(_PREFIX) and not e.redacted),
        key=lambda e: e.updated_at,
        reverse=True,
    )
    ids = [e.path[len(_PREFIX) :] for e in listed]
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
    """The "where we left off" block: every chosen session as log lines
    under an even share of `budget_chars` (the fold line says what is
    hidden), framed so the model knows the door and the recall call."""
    entries = await memory.list_documents(scope, prefix=_PREFIX)
    chosen = choose_sessions(entries, own=own, source=source)
    widths = CompactionConfig()
    share = max(budget_chars // max(len(chosen), 1), 1)
    blocks: list[str] = []
    for conversation_id in chosen:
        turns = await conversations.read_turns(conversation_id)
        if not turns:
            continue
        lines = project_conversation(
            turns,
            await conversations.read_projections(conversation_id),
            digest_chars=widths.digest_chars,
            user_chars=widths.user_chars,
            budget_chars=share,
            links=LinkRegistry.of(turns, source=conversation_id),
        )
        head = render(
            get_prompt("context.start_session"),
            conversation_id=conversation_id,
            actor=turns[-1].actor or "-",
        )
        blocks.append("\n".join([head, *lines]))
    body = "\n".join(blocks) if blocks else get_prompt("context.start_empty")
    return "\n".join(
        [get_prompt("context.start_header"), body, get_prompt("context.start_footer")]
    )


async def render_session_start(
    memory: MemoryStore,
    conversations: ConversationStore,
    settings: RecordSettings,
    *,
    session_id: str,
    source: str,
) -> str:
    """What the hook prints: the index, then where we left off."""
    index = await generate_memory_index(memory, settings.store.mounts)
    left_off = await render_left_off(
        memory, conversations, settings.mount.scope, own=session_id, source=source
    )
    return f"{get_prompt('context.start_index')}\n{index}\n\n{left_off}"
