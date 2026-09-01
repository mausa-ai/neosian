"""Memory maintenance — the gardener (DESIGN §16) — degrade-safe.

An explicit, operator-cadence pass over a store's writable mounts
(ledger #90 — no Conversation rider; reflection owns the session
boundary). Two stages: a deterministic stage that always runs and
executes only the byte-safe operations — merge byte-identical duplicates
(keep the oldest, delete the rest) and prune empty documents — so the
shell verb keeps a working keyless mode (ledger #91); and a model stage,
only when a model is given, that rules the judgment calls — semantic
merges, stale pruning, project→user promotion via rename,
confirm-or-decay — through one batched structured-output call (the
reflection idiom). Protection is enforced in code, never exhorted
(ledger #92): documents updated inside the age floor are never deleted,
redacted documents take no operation at all, read-only mounts are
excluded structurally, and edit-only mounts keep their document set —
edits only, so the deterministic stage (all deletes) skips them and the
model stage may not delete or rename there (NP). Every mutation runs
through the shared dispatcher
with the caller's actor, so each lands as an audited version row; spend
rides the result, never hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel

from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.mounts import MemoryConfig, Mount, resolve
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    MemoryStoreError,
)
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.structured import structured_call
from neosian._foundation.shared.types import AnyModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.llm.base import BaseLLMClient, Usage
    from neosian._foundation.memory.types import MemoryEntry

logger = logging.getLogger(__name__)

MAINTENANCE_MIN_AGE_DAYS: Final = 7


@dataclass(frozen=True, slots=True)
class MaintenanceWrite:
    """One landed maintenance action — the receipt. `path` is the virtual
    tool path (for a rename, the destination); `version` is the live
    document version after the action (None once deleted). The store's
    version rows are the durable audit trail behind it."""

    command: str
    path: str
    version: int | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """What one maintenance pass did: the actions that landed and the
    model spend it incurred (visible, never hidden — §16). `model=None`
    means the pass ran keylessly or the model call degraded; empty writes
    mean the store needed no gardening."""

    writes: tuple[MaintenanceWrite, ...] = ()
    usage: Usage | None = None
    model: str | None = None


# Per-command shapes with every field required — the OpenAI strict-mode
# rule (see reflection.py): a plain anyOf union, never oneOf, never a
# flat all-optional op. `rename` joins the set because promotion is a
# cross-mount rename; `insert` and `view` add nothing the payload does
# not already carry.
class MaintainCreateOp(BaseModel):
    command: Literal["create"]
    path: str
    content: str


class MaintainReplaceOp(BaseModel):
    command: Literal["str_replace"]
    path: str
    old_str: str
    new_str: str


class MaintainDeleteOp(BaseModel):
    command: Literal["delete"]
    path: str


class MaintainRenameOp(BaseModel):
    command: Literal["rename"]
    old_path: str
    new_path: str


MaintainOp = MaintainCreateOp | MaintainReplaceOp | MaintainDeleteOp | MaintainRenameOp


class MaintenanceBatch(BaseModel):
    ops: list[
        MaintainCreateOp | MaintainReplaceOp | MaintainDeleteOp | MaintainRenameOp
    ]


async def run_maintenance(
    config: MemoryConfig,
    *,
    acquire: Callable[[AnyModel], BaseLLMClient] | None = None,
    model: AnyModel | None = None,
    actor: str | None = None,
    clock: Clock | None = None,
    min_age: timedelta = timedelta(days=MAINTENANCE_MIN_AGE_DAYS),
) -> MaintenanceResult:
    """One maintenance pass over the config's writable mounts.

    Keyless without `acquire`/`model` (deterministic stage only); with
    both, the model stage follows. Degrade-safe throughout: a failed
    model call returns the deterministic result, a failed operation is
    skipped with a warning while the rest land.
    """
    if (acquire is None) != (model is None):
        raise ConfigurationError(
            "maintenance needs both acquire and model for the model stage, "
            "or neither for the deterministic-only pass"
        )
    if min_age < timedelta(0):
        raise ConfigurationError("min_age must not be negative")
    cutoff = (clock or SystemClock()).now() - min_age
    writes = list(await _deterministic_stage(config, actor, cutoff))
    if acquire is None or model is None:
        return MaintenanceResult(writes=tuple(writes))
    payload = await _render_evidence(config, cutoff)
    parsed, usage, api_model = await structured_call(
        acquire,
        model,
        get_prompt("maintenance.system"),
        payload,
        MaintenanceBatch,
        "Maintenance",
    )
    if parsed is None:
        return MaintenanceResult(writes=tuple(writes))
    for op in parsed.ops:
        write = await _execute(config, op, actor, cutoff)
        if write is not None:
            writes.append(write)
    return MaintenanceResult(writes=tuple(writes), usage=usage, model=api_model)


async def _deterministic_stage(
    config: MemoryConfig, actor: str | None, cutoff: datetime
) -> list[MaintenanceWrite]:
    """The byte-safe operations, per writable mount: prune empty
    documents and merge byte-identical duplicates keeping the earliest
    `created_at` (ties to the lexicographically first path). Deletion is
    gated on the age floor; redacted documents never enter. Edit-only
    mounts are skipped whole — every deterministic action is a delete,
    and their document set is fixed (NP)."""
    writes: list[MaintenanceWrite] = []
    for mount in config.mounts:
        if mount.read_only or mount.edit_only:
            continue
        by_content: dict[str, list[MemoryEntry]] = {}
        for entry in await config.store.list_documents(mount.scope):
            if entry.redacted:
                continue
            document = await config.store.read(mount.scope, entry.path)
            if document is None:
                continue
            if not document.content.strip():
                if entry.updated_at <= cutoff:
                    writes.extend(await _delete(config, mount, entry.path, actor))
                continue
            by_content.setdefault(document.content, []).append(entry)
        for duplicates in by_content.values():
            if len(duplicates) < 2:
                continue
            keeper = min(duplicates, key=lambda e: (e.created_at, e.path))
            for entry in duplicates:
                if entry is keeper or entry.updated_at > cutoff:
                    continue
                writes.extend(await _delete(config, mount, entry.path, actor))
    return writes


async def _delete(
    config: MemoryConfig, mount: Mount, doc_path: str, actor: str | None
) -> list[MaintenanceWrite]:
    path = f"/{mount.mount_path}/{doc_path}"
    result = await dispatch(config, "delete", {"path": path}, actor=actor)
    if not result.success:
        logger.warning("Maintenance delete on %s failed: %s", path, result.error)
        return []
    return [MaintenanceWrite(command="delete", path=path, version=None)]


async def _render_evidence(config: MemoryConfig, cutoff: datetime) -> str:
    """Every writable mount's live raw bodies, annotated with the aging
    evidence the model rules on (version, created/updated, protection
    markers). Bodies are raw so an emitted `old_str` matches stored
    content exactly; read-only mounts take no operations and are not
    shown; redacted documents are named but never read; edit-only mounts
    are shown annotated — their documents stay editable, their set does
    not change."""
    blocks = ["# Current memory"]
    for mount in config.mounts:
        if mount.read_only:
            continue
        header = f"## /{mount.mount_path}"
        if mount.description:
            header += f" — {mount.description}"
        if mount.edit_only:
            header += (
                " (edit-only — update existing documents;"
                " never create, delete, or rename one)"
            )
        lines = [header]
        entries = await config.store.list_documents(mount.scope)
        if not entries:
            lines.append("(empty)")
        for entry in entries:
            if entry.redacted:
                lines.append(
                    f"### /{mount.mount_path}/{entry.path}"
                    " (redacted — protected, take no action)"
                )
                continue
            document = await config.store.read(mount.scope, entry.path)
            if document is None:
                continue
            marker = (
                " [fresh — protected from deletion]"
                if entry.updated_at > cutoff
                else ""
            )
            lines.append(
                f"### /{mount.mount_path}/{entry.path}"
                f" [v{entry.version} | created {entry.created_at:%Y-%m-%d}"
                f" | updated {entry.updated_at:%Y-%m-%d}]{marker}"
            )
            lines.append(document.content)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def _execute(
    config: MemoryConfig, op: MaintainOp, actor: str | None, cutoff: datetime
) -> MaintenanceWrite | None:
    source = op.old_path if isinstance(op, MaintainRenameOp) else op.path
    reason = await _protected(
        config, source, cutoff, deletion=isinstance(op, MaintainDeleteOp)
    )
    if reason is None:
        reason = _fixed_set(config, op)
    if reason is not None:
        logger.warning("Maintenance %s on %s skipped: %s", op.command, source, reason)
        return None
    arguments = {k: v for k, v in op.model_dump().items() if k != "command"}
    result = await dispatch(config, op.command, arguments, actor=actor)
    if not result.success:
        logger.warning(
            "Maintenance %s on %s failed: %s", op.command, source, result.error
        )
        return None
    live = op.new_path if isinstance(op, MaintainRenameOp) else op.path
    # The receipt (NP) replaces the post-hoc re-read: for create,
    # str_replace and rename its version IS the live version; a delete
    # keeps the documented None (the receipt's int is the consumed row).
    # `path` stays the op's own spelling, never the receipt's canonical form.
    version = None
    if op.command != "delete" and result.receipt is not None:
        version = result.receipt.version
    return MaintenanceWrite(command=op.command, path=live, version=version)


async def _protected(
    config: MemoryConfig, path: str, cutoff: datetime, *, deletion: bool
) -> str | None:
    """The ledger #92 rules, enforced in code: redacted documents take no
    operation; the age floor blocks deletion (edits, creates and renames
    of fresh documents stay legal — content survives them). An unresolvable
    path returns None so the dispatcher produces its corrective failure."""
    try:
        mount, doc_path = resolve(config, path)
        document = await config.store.read(mount.scope, doc_path)
    except MemoryStoreError:
        return None
    if document is None:
        return None
    if document.redacted:
        return "the document is redacted"
    if deletion and document.updated_at > cutoff:
        return "the document is inside the age floor"
    return None


def _fixed_set(config: MemoryConfig, op: MaintainOp) -> str | None:
    """The edit-only rule (NP): a delete, or a rename touching an
    edit-only mount on either side, would change its fixed document set —
    refused with a named reason. Creates are left to the dispatcher: an
    overwrite is a legal edit there, and only the store knows whether the
    path already exists. An unresolvable path returns None so the
    dispatcher produces its corrective failure."""
    if isinstance(op, MaintainDeleteOp):
        paths: tuple[str, ...] = (op.path,)
    elif isinstance(op, MaintainRenameOp):
        paths = (op.old_path, op.new_path)
    else:
        return None
    for path in paths:
        try:
            mount, _ = resolve(config, path)
        except MemoryStoreError:
            continue
        if mount.edit_only:
            return f"/{mount.mount_path} is edit-only: its document set is fixed"
    return None
