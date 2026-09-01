"""Model distillation for compaction (DESIGN §9.6) — degrade-safe.

Two batched structured-output calls, both through a raw client obtained
from an injected ``acquire`` callable (ledger #32 — never a second Agent).
``acquire`` is a lease: the caller of `run_boundary` owns the client's
lifetime (ledger #33 — Conversation hands its session's cache; closing a
cached client here would leave a dead handle in the pool):
`distill` turns long assistant prose into one log line per turn
(k turns in → k lines out, keyed by explicit turn number so a partial
response can never misattribute), and `summarize_epochs` folds blocks of
warm lines into narrative epoch summaries. Any failure degrades — empty
result, warning logged — and the boundary continues; compaction never
loses the send. Spend is returned to the caller, never hidden.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from neosian._foundation.conversation.projection import one_line
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.structured import structured_call
from neosian._foundation.shared.types import AnyModel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.llm.base import BaseLLMClient, Usage

# Epoch summaries carry a whole block, so they get twice the per-turn
# digest budget (§9.6: length discipline comes from folding, not labels).
EPOCH_CHARS_FACTOR = 2


class DigestLine(BaseModel):
    turn: int
    line: str


class DigestBatch(BaseModel):
    lines: list[DigestLine]


class EpochSummary(BaseModel):
    last_turn: int
    summary: str


class EpochBatch(BaseModel):
    epochs: list[EpochSummary]


async def distill(
    items: Sequence[tuple[int, str]],
    *,
    acquire: Callable[[AnyModel], BaseLLMClient],
    model: AnyModel,
    digest_chars: int,
) -> tuple[dict[int, str], Usage | None, str | None]:
    """One line of digest per (turn, prose) item; {} on any failure."""
    payload = "\n\n".join(f"[{turn}]\n{prose}" for turn, prose in items)
    system = render(get_prompt("compaction.distill"), digest_chars=str(digest_chars))
    parsed, usage, api_model = await structured_call(
        acquire, model, system, payload, DigestBatch, "Compaction distillation"
    )
    if parsed is None:
        return {}, None, None
    wanted = {turn for turn, _ in items}
    digests = {
        line.turn: one_line(line.line, digest_chars)
        for line in parsed.lines
        if line.turn in wanted and line.line.strip()
    }
    return digests, usage, api_model


async def summarize_epochs(
    blocks: Sequence[tuple[int, Sequence[str]]],
    *,
    acquire: Callable[[AnyModel], BaseLLMClient],
    model: AnyModel,
    digest_chars: int,
) -> tuple[dict[int, str], Usage | None, str | None]:
    """One narrative summary per (last_turn, lines) block; {} on failure —
    an unfolded block is simply retried at the next boundary."""
    budget = EPOCH_CHARS_FACTOR * digest_chars
    payload = "\n\n".join(
        f"[block ending at turn {last_turn}]\n" + "\n".join(lines)
        for last_turn, lines in blocks
    )
    system = render(get_prompt("compaction.epoch"), epoch_chars=str(budget))
    parsed, usage, api_model = await structured_call(
        acquire, model, system, payload, EpochBatch, "Compaction epoch summarization"
    )
    if parsed is None:
        return {}, None, None
    wanted = {last_turn for last_turn, _ in blocks}
    summaries = {
        epoch.last_turn: one_line(epoch.summary, budget)
        for epoch in parsed.epochs
        if epoch.last_turn in wanted and epoch.summary.strip()
    }
    return summaries, usage, api_model
