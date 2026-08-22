"""The operator verbs' execution tier — `versions`, `redact`, `revert`.

The governance surface on the shell (DESIGN §14.2, NP): keyless audit
and remedy acts over the store, never dispatch commands and never agent
tools. `versions` exposes point-in-time reads (`--json` carries full
historical content — history reveals everything; `redact` is the only
eraser); `redact` is the one irreversible verb (C3's skeleton is not
restorable, and `revert` refuses redacted history); `revert` wraps
`revert_memory`. Each verb's `--json` prints its own envelope, never the
six-command `ToolResult` shape; tier-1 failures print a minimal
`{"error", "hint"}` object so `--json` keeps its one-JSON-object promise
(§14.1). None of them emits a `memory_write` event (#99 — off-stream
writes stay silent).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TextIO

from neosian._foundation.memory.dispatch import corrective
from neosian._foundation.memory.mounts import MemoryConfig, resolve, writable
from neosian._foundation.memory.revert import revert_memory
from neosian._foundation.memory.store_lifetime import open_store
from neosian._foundation.shared.exceptions import (
    MemoryPathInvalidError,
    MemoryStoreError,
)

if TYPE_CHECKING:
    from neosian._foundation.memory.cli_grammar import Request
    from neosian._foundation.tools.base import ToolResult


async def execute_operator(request: Request, *, out: TextIO, err: TextIO) -> int:
    try:
        async with open_store(request.settings) as store:
            config = MemoryConfig(store=store, mounts=request.settings.mounts)
            if request.command == "versions":
                return await _versions(config, request, out=out)
            if request.command == "redact":
                return await _redact(config, request, out=out)
            return await _revert(config, request, out=out, err=err)
    except MemoryStoreError as exc:
        return _fail(corrective(exc), request, out=out, err=err)


async def _versions(config: MemoryConfig, request: Request, *, out: TextIO) -> int:
    path = request.arguments["path"]
    mount, doc_path = resolve(config, path)
    if not doc_path:
        raise MemoryPathInvalidError(path, "a mount root is not a document")
    rows = await config.store.versions(mount.scope, doc_path, limit=request.limit)
    if request.json_output:
        envelope = {
            "path": f"/{mount.mount_path}/{doc_path}",
            "versions": [
                {
                    "version": row.version,
                    "action": row.action,
                    "actor": row.actor,
                    "created_at": row.created_at.isoformat(),
                    "redacted": row.redacted,
                    "content": row.content,
                }
                for row in rows
            ],
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    if not rows:
        # An audit query over history, not a document read: no rows is a
        # legitimate answer (exit 0), while an unknown mount fails above.
        out.write(f"no history for /{mount.mount_path}/{doc_path}\n")
        return 0
    for row in rows:
        marker = " (redacted)" if row.redacted else ""
        out.write(
            f"v{row.version}  {row.action}  {row.actor or '-'}"
            f"  {row.created_at.isoformat()}{marker}\n"
        )
    return 0


async def _redact(config: MemoryConfig, request: Request, *, out: TextIO) -> int:
    path = request.arguments["path"]
    mount, doc_path = resolve(config, path)
    writable(mount)  # a read-only mount refuses; edit-only is a content act
    target = None if request.scope_wide else doc_path
    matched = await config.store.redact(
        mount.scope, path=target, actor=request.settings.actor
    )
    count = f"{matched} document" if matched == 1 else f"{matched} documents"
    if request.json_output:
        envelope = {
            "path": path,
            "scope_wide": request.scope_wide,
            "matched": matched,
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    if request.scope_wide:
        # Scope-wide crosses mount paths: two mounts on one scope share
        # the erasure, so the scope itself is named.
        out.write(
            f"redacted {count} in scope {mount.scope!r} (mount /{mount.mount_path})\n"
        )
    else:
        out.write(f"redacted {count} at /{mount.mount_path}/{doc_path}\n")
    return 0


async def _revert(
    config: MemoryConfig, request: Request, *, out: TextIO, err: TextIO
) -> int:
    assert request.version is not None  # the grammar requires --version
    result = await revert_memory(
        config,
        request.arguments["path"],
        version=request.version,
        actor=request.settings.actor,
    )
    if not result.success:
        return _fail(result, request, out=out, err=err)
    receipt = result.receipt
    if request.json_output:
        envelope = {
            "command": "revert",
            "path": None if receipt is None else receipt.path,
            "version": None if receipt is None else receipt.version,
            "previous_path": None if receipt is None else receipt.previous_path,
            "detail": result.data,
        }
        out.write(json.dumps(envelope) + "\n")
        return 0
    if result.data:
        out.write(result.data if result.data.endswith("\n") else result.data + "\n")
    return 0


def _fail(
    result: ToolResult[str], request: Request, *, out: TextIO, err: TextIO
) -> int:
    """Tier 1: guidance on stderr; under --json also one minimal error
    object on stdout, keeping §14.1's one-JSON-object promise literal."""
    if request.json_output:
        envelope = {"error": result.error, "hint": result.system_reminder}
        out.write(json.dumps(envelope) + "\n")
    err.write(f"error: {result.error}\n")
    if result.system_reminder:
        err.write(f"hint: {result.system_reminder}\n")
    return 1
