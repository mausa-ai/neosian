"""Conversation views — another conversation as read-only context (§21).

A `ConversationView` names a conversation whose history is injected into
this one as a *fully* log-projected block: the winning §9.6 entry where
one exists, else the deterministic log line — a view never spends a
model call. It is rendered once per Conversation instance, like the
memory index, and refreshed at the compaction boundary; the newest
lines fit a char budget and the rest fold into one count line. Ids
carry no scope: which conversations are shareable is the host's duty,
never the library's (ledger #139).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.links import LinkRegistry
from neosian._foundation.conversation.projection import (
    entry_line,
    log_line,
    select,
)
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.index import INDEX_BUDGET_CHARS
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.prompt_assets import get_prompt, render

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.compaction import CompactionConfig
    from neosian._foundation.conversation.types import (
        ConversationProjection,
        ConversationTurn,
    )


@dataclass(frozen=True, slots=True)
class ConversationView:
    """A read-only, log-projected view of another conversation.

    `budget_chars` bounds the block (the index's default); the newest
    lines are kept and the rest fold into one line — paging, never
    deletion: `recall_turn(n, conversation=…)` re-reads any turn.
    """

    conversation_id: str
    budget_chars: int = INDEX_BUDGET_CHARS

    def __post_init__(self) -> None:
        parse_conversation_id(self.conversation_id)
        if self.budget_chars < 1:
            raise ConfigurationError(
                f"budget_chars must be >= 1, got {self.budget_chars}"
            )


def validated_views(
    views: Sequence[ConversationView], own: str
) -> tuple[ConversationView, ...]:
    """A view never names its own conversation, and never twice."""
    seen: set[str] = set()
    for view in views:
        if view.conversation_id == own:
            raise ConfigurationError(
                f"context= names this conversation itself ({own!r})"
            )
        if view.conversation_id in seen:
            raise ConfigurationError(f"context= names {view.conversation_id!r} twice")
        seen.add(view.conversation_id)
    return tuple(views)


def project_conversation(
    turns: Sequence[ConversationTurn],
    entries: Sequence[ConversationProjection],
    *,
    digest_chars: int,
    user_chars: int,
    budget_chars: int,
    links: LinkRegistry,
) -> list[str]:
    """Every turn as one log line, newest kept within `budget_chars`.

    Pure, no I/O, no model: a covered turn renders its winning entry,
    an uncovered one the deterministic line through `links` (§23). Over
    budget, the oldest lines fold into a single count line so the block
    always says how much it hides.
    """
    winner = select(entries)
    lines: list[tuple[int, int, str]] = []  # (first turn, last turn, line)
    emitted: set[int] = set()
    for turn in turns:
        index = winner.get(turn.turn)
        if index is None:
            text = log_line(
                turn, digest_chars=digest_chars, user_chars=user_chars, links=links
            )
            lines.append((turn.turn, turn.turn, f"[{turn.turn}] {text}"))
        elif index not in emitted:
            emitted.add(index)
            entry = entries[index]
            lines.append((entry.turn - entry.span + 1, entry.turn, entry_line(entry)))
    kept = 0
    used = 0
    for _, _, line in reversed(lines):
        if used + len(line) + 1 > budget_chars:
            break
        used += len(line) + 1
        kept += 1
    if kept == len(lines):
        return [line for _, _, line in lines]
    folded = lines[: len(lines) - kept]
    fold = render(
        get_prompt("context.view_fold"),
        first=str(folded[0][0]),
        last=str(folded[-1][1]),
        count=str(folded[-1][1] - folded[0][0] + 1),
    )
    return [fold, *(line for _, _, line in lines[len(lines) - kept :])]


async def render_views(
    store: ConversationStore,
    views: Sequence[ConversationView],
    *,
    config: CompactionConfig,
) -> tuple[list[Message], list[LinkRegistry]]:
    """One synthetic USER block per view, in the order given, and each
    view's registry — its handles are qualified by its id (§23).

    Projected the way this conversation would compact its own history
    (`config`'s digest and user widths); an empty source still renders
    its frame, so the model knows the view exists.
    """
    out: list[Message] = []
    registries: list[LinkRegistry] = []
    for view in views:
        turns = await store.read_turns(view.conversation_id)
        entries = await store.read_projections(view.conversation_id)
        links = LinkRegistry.of(turns, source=view.conversation_id)
        registries.append(links)
        lines = project_conversation(
            turns,
            entries,
            digest_chars=config.digest_chars,
            user_chars=config.user_chars,
            budget_chars=view.budget_chars,
            links=links,
        )
        body = "\n".join(lines) if lines else get_prompt("context.view_empty")
        header = render(
            get_prompt("context.view_header"), conversation_id=view.conversation_id
        )
        footer = render(
            get_prompt("context.view_footer"), conversation_id=view.conversation_id
        )
        out.append(Message(role=Role.USER, content=f"{header}\n{body}\n{footer}"))
    return out, registries
