"""The handoff: `continue_session` and `handoff` (DESIGN §33, N6).

A handoff is declared, never assumed. On departure the agent writes the
baton with `handoff(note)`: one document, `handoff`, at the sessions
mount's root, pending until a session picks it up and never deleted. On
arrival the agent makes one call, `continue_session(conversation=None)`:
the conversation delivered under a budget — every user prompt and final
answer verbatim, tool rounds one line each, no model in the room — with
the pending note when it is that session's, and the baton marked picked
up. A bare call means the note's session when the record has linked it,
the newest session listed after the note was written when it has not,
else the newest listed session that is not the caller's own. Lineage is
not written here: the record verb and the Conversation read it back from
the turn (`memory/sessions.py`), because these tools run where the
session id is unknown; both results begin with the header that read
pairs with the call.

This module deliberately has no `from __future__ import annotations`:
the @Tool decorator resolves the signature's hints at decoration time.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.links import LinkRegistry
from neosian._foundation.conversation.projection import (
    REDACTED,
    one_line,
    tool_segment,
)
from neosian._foundation.conversation.recall import Reach, in_reach
from neosian._foundation.conversation.types import (
    ConversationProjection,
    ConversationTurn,
)
from neosian._foundation.conversation.views import project_conversation
from neosian._foundation.llm.base import Role, text_of
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.sessions import (
    CONTINUE_TOOL,
    HANDOFF_NOTE_CHARS,
    HANDOFF_PATH,
    HANDOFF_TOOL,
    SESSIONS_PREFIX,
    Handoff,
    handoff_document,
    parse_handoff,
    parse_sessions_document,
    sessions_path,
)
from neosian._foundation.memory.types import MemoryDocument
from neosian._foundation.messaging.core import Mailbox
from neosian._foundation.messaging.history import continuation_context
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    ConversationStoreError,
    MemoryConflictError,
    MemoryStoreError,
)
from neosian._foundation.shared.prompt_assets import (
    get_prompt,
    get_prompt_params,
    render,
)
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

# Four times the index budget: the newest turns verbatim under three
# quarters of it, the older ones as log lines under the rest (§9.6).
CONTINUE_BUDGET_CHARS: Final = 32768
_WALL_CLOCK: Final = SystemClock()
Actor = str | None | Callable[[], str]


def render_continuation(
    turns: Sequence[ConversationTurn],
    entries: Sequence[ConversationProjection],
    *,
    continues: Sequence[str] = (),
    note: Handoff | None = None,
    budget_chars: int = CONTINUE_BUDGET_CHARS,
) -> str:
    """The conversation delivered: the note first when there is one, the
    older turns as log lines, the newest verbatim (always the last one),
    framed so the reader knows it is carrying on. Pure, no model."""
    conversation_id = turns[-1].conversation_id
    widths = CompactionConfig()
    links = LinkRegistry.of(turns, source=conversation_id)
    verbatim: list[str] = []
    spent = 0
    for turn in reversed(turns):
        block = _verbatim(turn, widths.digest_chars, links)
        if verbatim and spent + len(block) > budget_chars * 3 // 4:
            break
        verbatim.append(block)
        spent += len(block)
    verbatim.reverse()
    older = turns[: len(turns) - len(verbatim)]
    lines = (
        project_conversation(
            older,
            entries,
            digest_chars=widths.digest_chars,
            user_chars=widths.user_chars,
            budget_chars=max(budget_chars - spent, 1),
            links=links,
        )
        if older
        else []
    )
    head = render(
        get_prompt("context.continue_header"),
        conversation_id=conversation_id,
        actor=turns[-1].actor or "-",
        turns=str(turns[-1].turn),
        first=_stamp(turns[0].created_at),
        last=_stamp(turns[-1].created_at),
        continues=(
            render(get_prompt("context.continue_continues"), ids=", ".join(continues))
            if continues
            else ""
        ),
    )
    noted = (
        []
        if note is None
        else [
            render(
                get_prompt("context.continue_note"),
                written=_stamp(note.written),
                actor=note.actor,
            ),
            note.note,
            get_prompt("context.continue_note_end"),
        ]
    )
    footer = get_prompt("context.continue_footer")
    return "\n".join([head, *noted, *lines, *verbatim, footer])


def _verbatim(turn: ConversationTurn, digest_chars: int, links: LinkRegistry) -> str:
    """`[n] USER:` verbatim, each tool round one line, the final answer
    verbatim and any earlier prose one line (the agent thinking aloud);
    a redacted turn is its label and the one word (§38)."""
    if turn.redacted:
        return f"[{turn.turn}] {REDACTED}"
    results = {m.tool_call_id: m for m in turn.messages if m.role is Role.TOOL}
    prose = [
        m for m in turn.messages if m.role is Role.ASSISTANT and text_of(m).strip()
    ]
    lines: list[str] = []
    for message in turn.messages:
        if message.role is Role.USER:
            lines.append(f"[{turn.turn}] USER: {text_of(message)}")
        elif message.role is Role.ASSISTANT:
            text = text_of(message).strip()
            if text:
                final = message is prose[-1]
                lines.append(
                    "AGENT: "
                    + (text if final else one_line(text, digest_chars, links=links))
                )
            for call in message.tool_calls:
                lines.append(
                    tool_segment(call.name, call.arguments, results.get(call.id), links)
                )
    return "\n".join(lines)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="minutes").replace("+00:00", "Z")


def handoff_tools(
    store: ConversationStore,
    memory: MemoryStore,
    scope: str,
    *,
    actor: Actor,
    own: str | None = None,
    reach: Reach | None = None,
    where: str = "",
    clock: Clock = _WALL_CLOCK,
    mailbox: Mailbox | None = None,
) -> tuple[ToolFunction, ToolFunction]:
    """`(continue_session, handoff)` over `scope`'s sessions and baton.
    `own` is the caller's conversation when it has one (never the default,
    linked into the baton it writes); `reach` with `where` is what a named
    conversation must be in, None any id the store holds (the host's duty,
    #139); `actor` is resolved per call when it is a callable."""

    def stamped() -> str | None:
        return actor() if callable(actor) else actor

    @Tool(
        name=CONTINUE_TOOL,
        description=get_prompt("tools.continue_session"),
        params=get_prompt_params("tools.continue_session_params"),
    )
    async def continue_session(
        conversation: str | None = None, session: str | None = None
    ) -> ToolResult[str]:
        if own is not None and session not in (None, own):
            return ToolResult.fail("calling session differs from bound conversation")
        outcome = await continue_conversation(
            store,
            memory,
            scope,
            conversation,
            own=own,
            reach=reach,
            where=where,
            by=stamped(),
            clock=clock,
        )
        if isinstance(outcome, ToolResult):
            return outcome
        text = outcome.text
        if mailbox is not None:
            reader = Mailbox(
                mailbox.memory,
                session=own or session,
                actor=mailbox.actor,
                config=mailbox.config,
                clock=mailbox.clock,
            )
            extra = await continuation_context(reader, scope, outcome.conversation)
            if extra:
                text += "\n\n" + extra
        return ToolResult.ok(text)

    @Tool(
        name=HANDOFF_TOOL,
        description=get_prompt("tools.handoff"),
        params=get_prompt_params("tools.handoff_params"),
    )
    async def handoff(note: str) -> ToolResult[str]:
        text = note.strip()
        if not text:
            return ToolResult.fail(
                "Write the note first",
                system_reminder=get_prompt("context.handoff_guide"),
            )
        if len(text) > HANDOFF_NOTE_CHARS:
            return ToolResult.fail(
                f"The note is {len(text)} characters; keep it under "
                f"{HANDOFF_NOTE_CHARS}",
                system_reminder=get_prompt("context.handoff_guide"),
            )
        writer = stamped()
        try:
            await memory.write(
                scope,
                HANDOFF_PATH,
                handoff_document(
                    actor=writer or "-",
                    written=clock.now(),
                    note=text,
                    conversation=own,
                ),
                actor=writer,
            )
        except MemoryStoreError as exc:
            return ToolResult.fail(f"[{exc.code}] {exc.message}")
        return ToolResult.ok(get_prompt("context.handoff_recorded"))

    return continue_session, handoff


@dataclass(frozen=True, slots=True)
class Continuation:
    """What a continue call delivered: the conversation, the text, and
    whether its pending note rode along."""

    conversation: str
    text: str
    note: bool


async def continue_conversation(
    store: ConversationStore,
    memory: MemoryStore,
    scope: str,
    conversation: str | None,
    *,
    own: str | None = None,
    reach: Reach | None = None,
    where: str = "",
    by: str | None = None,
    clock: Clock = _WALL_CLOCK,
    pick_up: bool = True,
) -> Continuation | ToolResult[str]:
    """The continue call's work (§33): the target resolved, delivered with
    its pending note, and the baton marked picked up by `by` — unless
    `pick_up` is off, the shell's read (a terminal is not a session). A
    corrective failure comes back as the `ToolResult` the tool returns."""
    try:
        document = await memory.read(scope, HANDOFF_PATH)
        baton = _pending(document)
        if conversation is None:
            chosen = await _default_target(memory, scope, baton, own)
            if isinstance(chosen, ToolResult):
                return chosen
            target = chosen
        else:
            if reach is not None:
                ids = await in_reach(
                    reach, conversation, tool=CONTINUE_TOOL, where=where
                )
                if isinstance(ids, ToolResult):
                    return ids
            target = conversation
        turns = await store.read_turns(target)
        if not turns:
            return ToolResult.fail(f"Conversation {target!r} has no turns")
        listed = await memory.read(scope, sessions_path(target))
        continues = (
            () if listed is None else parse_sessions_document(listed.content).continues
        )
        delivered = (
            baton if _belongs(baton, target, named=conversation is not None) else None
        )
        if pick_up and document is not None and delivered is not None:
            await _pick_up(memory, scope, document, delivered, target, by, clock)
        text = render_continuation(
            turns,
            await store.read_projections(target),
            continues=continues,
            note=delivered,
        )
        return Continuation(target, text, delivered is not None)
    except (ConversationStoreError, MemoryStoreError) as exc:
        return ToolResult.fail(f"[{exc.code}] {exc.message}")


def _pending(document: MemoryDocument | None) -> Handoff | None:
    if document is None or document.redacted:
        return None
    baton = parse_handoff(document.content)
    return baton if baton is not None and baton.pending else None


def _belongs(baton: Handoff | None, target: str, *, named: bool) -> bool:
    """Whether the pending note is the delivered session's: linked to it,
    or unlinked when the call named nothing (the default chose it)."""
    if baton is None:
        return False
    return baton.conversation == target or (baton.conversation is None and not named)


async def _default_target(
    memory: MemoryStore, scope: str, baton: Handoff | None, own: str | None
) -> str | ToolResult[str]:
    """The session a bare call means (the module docstring's rule)."""
    if baton is not None and baton.conversation is not None:
        return baton.conversation
    entries = sorted(
        (
            e
            for e in await memory.list_documents(scope, prefix=SESSIONS_PREFIX)
            if not e.redacted and e.path[len(SESSIONS_PREFIX) :] != own
        ),
        key=lambda e: e.updated_at,
        reverse=True,
    )
    if baton is not None:
        entries = [e for e in entries if e.updated_at > baton.written]
        if not entries:
            return ToolResult.fail(
                "The handoff note's session has not landed yet",
                system_reminder="Name the conversation, or retry in a moment.",
            )
    if not entries:
        return ToolResult.fail(
            "Nothing to continue: no session is listed in this scope",
            system_reminder="Name a conversation id to continue one anyway.",
        )
    return entries[0].path[len(SESSIONS_PREFIX) :]


async def _pick_up(
    memory: MemoryStore,
    scope: str,
    document: MemoryDocument,
    baton: Handoff,
    target: str,
    by: str | None,
    clock: Clock,
) -> None:
    """The baton marked picked up by a new version, against the version
    that was read; on a conflict (the departing Stop linking it in the
    same beat) once more against the fresh one, still pending or not."""
    for attempt in range(2):
        content = handoff_document(
            actor=baton.actor,
            written=baton.written,
            note=baton.note,
            conversation=target,
            picked_up=(clock.now(), by or "-"),
        )
        try:
            await memory.write(
                scope,
                HANDOFF_PATH,
                content,
                actor=by,
                expected_version=document.version,
            )
            return
        except MemoryConflictError:
            if attempt:
                raise
            fresh = await memory.read(scope, HANDOFF_PATH)
            again = _pending(fresh)
            if fresh is None or again is None:
                return
            document, baton = fresh, again
