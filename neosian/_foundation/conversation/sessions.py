"""A Conversation's own place in the project's sessions (DESIGN §32,
ledger #294).

Conversations carry no scope, so "this project's sessions" is the
`sessions/` listing of the mount at `/project`, which the record verb
wrote for foreign agents alone. A Conversation with a project mount now
lists itself: after each persisted turn it writes `sessions/<id>` (agent
`neosian`, started, the last prompt, the turn count) by `<actor>#<turn>`
— a library write, never through the dispatcher, silent, and never
raising: the turn is already stored, and a raise would report a failed
send over a persisted turn. The same listing is the reach the recall and
search pair reads live.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from neosian._foundation.llm.base import text_of
from neosian._foundation.memory.sessions import (
    project_mount,
    session_ids,
    sessions_document,
    sessions_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.conversation.recall import Reach
    from neosian._foundation.conversation.types import ConversationTurn
    from neosian._foundation.llm.base import Message
    from neosian._foundation.memory.mounts import MemoryConfig

AGENT: Final = "neosian"
logger = logging.getLogger(__name__)


def project_sessions(memory: MemoryConfig | None) -> Reach | None:
    """The project's listed sessions, read live; None without a mount at
    `/project` (a read-only one is still listed)."""
    mount = project_mount(memory)
    if memory is None or mount is None:
        return None
    store, scope = memory.store, mount.scope

    async def listed() -> Sequence[str]:
        return await session_ids(store, scope)

    return listed


async def record_session(
    memory: MemoryConfig | None,
    turns: Sequence[ConversationTurn],
    user: Message,
    *,
    actor: str,
) -> None:
    """Write the conversation's sessions document after `turns[-1]`
    landed, when a writable mount at `/project` exists; log, never raise."""
    mount = project_mount(memory)
    if memory is None or mount is None or mount.read_only or mount.edit_only:
        return
    turn = turns[-1]
    document = sessions_document(
        agent=AGENT,
        session_id=turn.conversation_id,
        started=turns[0].created_at,
        last_prompt=text_of(user),
        turns=turn.turn,
    )
    try:
        await memory.store.write(
            mount.scope,
            sessions_path(turn.conversation_id),
            document,
            actor=f"{actor}#{turn.turn}",
        )
    except Exception:
        logger.warning(
            "the sessions document of %r was not written",
            turn.conversation_id,
            exc_info=True,
        )
