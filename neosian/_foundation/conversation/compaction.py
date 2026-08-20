"""Compaction v1 — trigger and boundary orchestration (DESIGN §9.6).

The boundary projects every uncovered turn older than the hot band:
deterministic log lines are free; long assistant prose goes through one
batched distillation call; due epoch blocks fold into model-written
summaries. Entries checkpoint via ``append_projections`` — computed per
turn, once; the checkpoint head is derived, never stored. A fold
supersedes without mutation; entries are never deleted.

Everything here is agent-free (ledger #32): the model calls ride the
injected ``acquire`` callable, and Conversation owns the trigger site,
the agent rebuild, and the usage fold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from neosian._foundation.conversation.distill import distill, summarize_epochs
from neosian._foundation.conversation.projection import (
    agent_prose,
    entry_line,
    log_line,
    needs_distillation,
    select,
)
from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import ModelUsage, Usage
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import Model

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.conversation.types import ConversationTurn
    from neosian._foundation.llm.base import BaseLLMClient, Message
    from neosian._foundation.shared.context_policy import ContextPolicy
    from neosian._foundation.shared.types import Provider

# USER text is compacted least aggressively (§9.6): verbatim up to this
# multiple of digest_chars, then head-clipped with a recall pointer
# (ledger #31 — unbounded would make the view un-shrinkable).
USER_CHARS_FACTOR = 4


@dataclass(frozen=True, slots=True)
class CompactionConfig:
    """Configuration for the log-projection compactor (§9.6).

    Default-on (ledger #28): `Conversation(compaction=None)` resolves to
    this default instance. `enabled` gates the automatic trigger only —
    an explicit `Conversation.compact()` always runs. `model=None` means
    the agent's own model.
    """

    enabled: bool = True
    model: Model | None = None
    hot_turns: int = 8
    trigger_fraction: float = 0.75
    digest_chars: int = 200
    epoch_turns: int = 20
    recall_tool: bool = True

    def __post_init__(self) -> None:
        if self.hot_turns < 1:
            raise ConfigurationError(f"hot_turns must be >= 1, got {self.hot_turns}")
        if self.epoch_turns < 1:
            raise ConfigurationError(
                f"epoch_turns must be >= 1, got {self.epoch_turns}"
            )
        if self.digest_chars < 1:
            raise ConfigurationError(
                f"digest_chars must be >= 1, got {self.digest_chars}"
            )
        if not 0 < self.trigger_fraction <= 1:
            raise ConfigurationError(
                f"trigger_fraction must be in (0, 1], got {self.trigger_fraction}"
            )

    @property
    def user_chars(self) -> int:
        return USER_CHARS_FACTOR * self.digest_chars


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """What one boundary did: the entries it checkpointed and the model
    spend it incurred (visible, never hidden — §9.6). Empty entries mean
    the boundary had nothing to project."""

    entries: tuple[ConversationProjection, ...] = ()
    usage: Usage | None = None
    model: str | None = None


def should_compact(
    messages: Sequence[Message],
    *,
    policy: ContextPolicy,
    model: Model,
    fraction: float,
) -> bool:
    """High-water check: the deliberate underestimate stays the safe
    direction; the reactive 400 wrap stays the backstop."""
    return policy.estimate_tokens(messages) > int(model.context_window * fraction)


def merge_usage(
    existing: tuple[ModelUsage, ...], model: str, usage: Usage
) -> tuple[ModelUsage, ...]:
    """Fold one model's usage into a per-model ledger tuple."""
    merged = list(existing)
    for index, entry in enumerate(merged):
        if entry.model == model:
            merged[index] = ModelUsage(model=model, usage=entry.usage + usage)
            return tuple(merged)
    merged.append(ModelUsage(model=model, usage=usage))
    return tuple(merged)


async def run_boundary(
    *,
    store: ConversationStore,
    conversation_id: str,
    turns: Sequence[ConversationTurn],
    projections: Sequence[ConversationProjection],
    config: CompactionConfig,
    model: Model,
    acquire: Callable[[Provider], BaseLLMClient],
) -> CompactionResult:
    """Run one compaction boundary; never more than one per send.

    Distillation failure degrades the affected turns to deterministic
    ``kind="log"`` lines; epoch failure skips the fold (retried next
    boundary); a store failure propagates — the caller's send raises
    having persisted nothing.
    """
    head = turns[-1].turn if turns else 0
    cutoff = head - config.hot_turns
    covered = set(select(projections))
    pending = [t for t in turns if t.turn <= cutoff and t.turn not in covered]

    usage: Usage | None = None
    api_model: str | None = None
    new_entries: list[ConversationProjection] = []

    if pending:
        to_distill = [
            (t.turn, agent_prose(t))
            for t in pending
            if needs_distillation(t, digest_chars=config.digest_chars)
        ]
        digests: dict[int, str] = {}
        if to_distill:
            digests, usage, api_model = await distill(
                to_distill,
                acquire=acquire,
                model=config.model or model,
                digest_chars=config.digest_chars,
            )
        for turn in pending:
            override = digests.get(turn.turn)
            new_entries.append(
                ConversationProjection(
                    turn=turn.turn,
                    kind="digest" if override is not None else "log",
                    text=log_line(
                        turn,
                        digest_chars=config.digest_chars,
                        user_chars=config.user_chars,
                        agent_override=override,
                    ),
                )
            )

    combined = [*projections, *new_entries]
    blocks = _due_epoch_blocks(combined, cutoff=cutoff, epoch_turns=config.epoch_turns)
    if blocks:
        summaries, epoch_usage, epoch_model = await summarize_epochs(
            blocks,
            acquire=acquire,
            model=config.model or model,
            digest_chars=config.digest_chars,
        )
        for last_turn, _ in blocks:
            summary = summaries.get(last_turn)
            if summary is not None:
                new_entries.append(
                    ConversationProjection(
                        turn=last_turn,
                        kind="epoch",
                        text=summary,
                        span=config.epoch_turns,
                    )
                )
        if epoch_usage is not None:
            usage = epoch_usage if usage is None else usage + epoch_usage
            api_model = api_model or epoch_model

    if new_entries:
        await store.append_projections(conversation_id, new_entries)
    return CompactionResult(entries=tuple(new_entries), usage=usage, model=api_model)


def _due_epoch_blocks(
    entries: Sequence[ConversationProjection], *, cutoff: int, epoch_turns: int
) -> list[tuple[int, list[str]]]:
    """Fixed-aligned blocks [k·E+1 … (k+1)·E] wholly past the cutoff,
    fully covered, and not already folded — with their selected lines."""
    winner = select(entries)
    folded = {
        entry.turn
        for entry in entries
        if entry.kind == "epoch" and entry.span == epoch_turns
    }
    blocks: list[tuple[int, list[str]]] = []
    last_turn = epoch_turns
    while last_turn <= cutoff:
        if last_turn not in folded:
            lines: list[str] = []
            emitted: set[int] = set()
            for turn in range(last_turn - epoch_turns + 1, last_turn + 1):
                index = winner.get(turn)
                if index is None:
                    lines = []
                    break
                if index not in emitted:
                    emitted.add(index)
                    lines.append(entry_line(entries[index]))
            if lines:
                blocks.append((last_turn, lines))
        last_turn += epoch_turns
    return blocks
