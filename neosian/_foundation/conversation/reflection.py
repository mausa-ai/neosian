"""Reflection — session-boundary auto-memory (DESIGN §15) — degrade-safe.

End-of-conversation distillation: one batched structured-output call
(the distill idiom, through the injected ``acquire`` lease) decides what
from the session is worth keeping, and the emitted operations execute as
deliberate writes through the shared memory dispatcher — audited
(``actor = conversation_id``), dedup-disciplined (the payload shows every
writable mount's live documents, so the model updates what it can see
instead of duplicating it — fenced and budgeted by memory/payload.py,
since bodies and transcript are data, never instructions). The model
call failing degrades to an empty result carrying the reason; a failed
operation is skipped with a warning and the rest still land; a delete
respects the gardener's protections (the age floor, redaction). Spend is
returned on the result, never hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from neosian._foundation.conversation.projection import render_turn
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.maintenance import (
    MAINTENANCE_MIN_AGE_DAYS,
    protection_reason,
)
from neosian._foundation.memory.payload import fenced, new_fence, render_documents
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.prompt_assets import get_prompt, render
from neosian._foundation.shared.structured import structured_call
from neosian._foundation.shared.types import AnyModel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from neosian._foundation.conversation.types import ConversationTurn
    from neosian._foundation.llm.base import BaseLLMClient, Usage
    from neosian._foundation.memory.mounts import MemoryConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReflectionConfig:
    """Configuration for session-boundary reflection (§15).

    Default-on: `Conversation(reflection=None)` resolves to this default
    instance, and reflection is inert without memory mounts. `enabled`
    gates the `aclose()` rider only — an explicit `Conversation.reflect()`
    always runs. `model=None` means the agent's own model.
    """

    enabled: bool = True
    model: AnyModel | None = None


@dataclass(frozen=True, slots=True)
class ReflectionWrite:
    """One landed reflection write — the receipt. `version` is the live
    document version after the write (None once deleted); the store's
    version rows are the durable audit trail behind it."""

    command: str
    path: str
    version: int | None = None


@dataclass(frozen=True, slots=True)
class ReflectionResult:
    """What one reflection pass did: the writes that landed and the model
    spend it incurred (visible, never hidden — §15). `degraded` names the
    failure when the distillation call did not land (the turns stay
    pending for a retry); `model=None` with `degraded=None` means it never
    ran; empty writes under a model mean the session held nothing worth
    keeping."""

    writes: tuple[ReflectionWrite, ...] = ()
    usage: Usage | None = None
    model: str | None = None
    degraded: str | None = None


# Per-command shapes with every field required: OpenAI's strict mode
# rejects any schema whose `required` omits a property, and a flat
# all-optional op would either 400 there or force explicit nulls from
# providers that don't validate. A plain (non-discriminated) union keeps
# the wire schema on `anyOf`, which strict mode accepts; `oneOf` is not.
class CreateOp(BaseModel):
    command: Literal["create"]
    path: str
    content: str


class ReplaceOp(BaseModel):
    command: Literal["str_replace"]
    path: str
    old_str: str
    new_str: str


class DeleteOp(BaseModel):
    command: Literal["delete"]
    path: str


ReflectionOp = CreateOp | ReplaceOp | DeleteOp


class ReflectionBatch(BaseModel):
    ops: list[CreateOp | ReplaceOp | DeleteOp]


async def run_reflection(
    *,
    memory_config: MemoryConfig,
    turns: Sequence[ConversationTurn],
    acquire: Callable[[AnyModel], BaseLLMClient],
    model: AnyModel,
    actor: str | None,
    clock: Clock | None = None,
    min_age: timedelta = timedelta(days=MAINTENANCE_MIN_AGE_DAYS),
) -> ReflectionResult:
    """One reflection pass over the given turns; degrade-safe, except
    that a `ConfigurationError` from the lease propagates (the #84 rule).
    Deletes respect the gardener's age floor from `clock` and `min_age`."""
    if not turns:
        return ReflectionResult()
    fence = new_fence()
    memory = await render_documents(
        memory_config,
        fence=fence,
        edit_only_note="edit-only — update existing documents; never add or remove one",
    )
    transcript = fenced(fence, "\n\n".join(render_turn(turn) for turn in turns))
    parsed, usage, api_model, degraded = await structured_call(
        acquire,
        model,
        render(get_prompt("reflection.system"), fence=fence),
        "\n\n".join((memory, "# Session transcript", transcript)),
        ReflectionBatch,
        "Reflection",
    )
    if parsed is None:
        return ReflectionResult(degraded=degraded)
    cutoff = (clock or SystemClock()).now() - min_age
    writes: list[ReflectionWrite] = []
    for op in parsed.ops:
        write = await _execute(memory_config, op, actor, cutoff)
        if write is not None:
            writes.append(write)
    return ReflectionResult(writes=tuple(writes), usage=usage, model=api_model)


async def _execute(
    config: MemoryConfig, op: ReflectionOp, actor: str | None, cutoff: datetime
) -> ReflectionWrite | None:
    # The gardener's protections (ledger #92) hold here too: the more
    # frequent pass must not carry the weaker guard.
    reason = await protection_reason(
        config, op.path, cutoff, deletion=isinstance(op, DeleteOp)
    )
    if reason is not None:
        logger.warning("Reflection %s on %s skipped: %s", op.command, op.path, reason)
        return None
    arguments = {k: v for k, v in op.model_dump().items() if k != "command"}
    result = await dispatch(config, op.command, arguments, actor=actor)
    if not result.success:
        logger.warning(
            "Reflection %s on %s failed: %s", op.command, op.path, result.error
        )
        return None
    # The receipt (NP) replaces the post-hoc re-read: for create and
    # str_replace its version IS the live version; a delete keeps the
    # documented None. `path` stays the op's own spelling, never the
    # receipt's canonical form.
    version = None
    if op.command != "delete" and result.receipt is not None:
        version = result.receipt.version
    return ReflectionWrite(command=op.command, path=op.path, version=version)
