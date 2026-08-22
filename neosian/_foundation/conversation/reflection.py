"""Reflection — session-boundary auto-memory (DESIGN §15) — degrade-safe.

End-of-conversation distillation: one batched structured-output call
(the distill idiom, through the injected ``acquire`` lease) decides what
from the session is worth keeping, and the emitted operations execute as
deliberate writes through the shared memory dispatcher — audited
(``actor = conversation_id``), dedup-disciplined (the payload shows every
writable mount's live documents, so the model updates what it can see
instead of duplicating it). The model call failing degrades to an empty
result with a warning; a failed operation is skipped with a warning and
the rest still land. Spend is returned on the result, never hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from neosian._foundation.conversation.projection import render_turn
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.mounts import resolve
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.structured import structured_call
from neosian._foundation.shared.types import Model

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.conversation.types import ConversationTurn
    from neosian._foundation.llm.base import BaseLLMClient, Usage
    from neosian._foundation.memory.mounts import MemoryConfig
    from neosian._foundation.shared.types import Provider

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
    model: Model | None = None


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
    spend it incurred (visible, never hidden — §15). `model=None` means
    the distillation call failed or never ran; empty writes under a model
    mean the session held nothing worth keeping."""

    writes: tuple[ReflectionWrite, ...] = ()
    usage: Usage | None = None
    model: str | None = None


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
    acquire: Callable[[Provider], BaseLLMClient],
    model: Model,
    actor: str | None,
) -> ReflectionResult:
    """One reflection pass over the given turns; degrade-safe."""
    if not turns:
        return ReflectionResult()
    payload = "\n\n".join(
        (
            await _render_memory(memory_config),
            "# Session transcript",
            "\n\n".join(render_turn(turn) for turn in turns),
        )
    )
    parsed, usage, api_model = await structured_call(
        acquire,
        model,
        get_prompt("reflection.system"),
        payload,
        ReflectionBatch,
        "Reflection",
    )
    if parsed is None:
        return ReflectionResult()
    writes: list[ReflectionWrite] = []
    for op in parsed.ops:
        write = await _execute(memory_config, op, actor)
        if write is not None:
            writes.append(write)
    return ReflectionResult(writes=tuple(writes), usage=usage, model=api_model)


async def _render_memory(config: MemoryConfig) -> str:
    """Every writable mount with its live document bodies — the dedup
    evidence. Bodies are raw (no line numbers), so an emitted `old_str`
    matches stored content exactly. Read-only mounts are not shown: no
    operation may target them."""
    blocks = ["# Current memory"]
    for mount in config.mounts:
        if mount.read_only:
            continue
        header = f"## /{mount.mount_path}"
        if mount.description:
            header += f" — {mount.description}"
        lines = [header]
        entries = await config.store.list_documents(mount.scope)
        if not entries:
            lines.append("(empty)")
        for entry in entries:
            if entry.redacted:
                lines.append(f"### /{mount.mount_path}/{entry.path} (redacted)")
                continue
            document = await config.store.read(mount.scope, entry.path)
            if document is not None:
                lines.append(f"### /{mount.mount_path}/{entry.path}")
                lines.append(document.content)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _execute(
    config: MemoryConfig, op: ReflectionOp, actor: str | None
) -> ReflectionWrite | None:
    arguments = {k: v for k, v in op.model_dump().items() if k != "command"}
    result = await dispatch(config, op.command, arguments, actor=actor)
    if not result.success:
        logger.warning(
            "Reflection %s on %s failed: %s", op.command, op.path, result.error
        )
        return None
    return ReflectionWrite(
        command=op.command,
        path=op.path,
        version=await _live_version(config, op.path),
    )


async def _live_version(config: MemoryConfig, path: str) -> int | None:
    try:
        mount, doc_path = resolve(config, path)
        document = await config.store.read(mount.scope, doc_path)
    except MemoryStoreError:
        return None
    return None if document is None else document.version
