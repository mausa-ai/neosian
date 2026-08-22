"""Undo one memory write from its version rows (NP).

`revert_memory(config, path, version=N)` inverts the mutation recorded
at version row `N` — the number a `memory_write` event (or a
`MemoryWriteReceipt`) carries. One rule collapses every case: *if there
was no live document before row N, delete; otherwise write row N-1's
content back.* The inverse executes through the shared dispatcher, so
read-only enforcement, path validation, corrective mapping and the audit
row all apply — a revert **appends** a new version row, never rewrites
history. Undoing a rename is therefore two reverts (the destination's
`created`, then the source's `deleted`) — per-document by design; a
cross-mount composition has no transaction to invert (C1).

Guards: the target must be the newest row (`memory_conflict`, reason
`revert_stale` — no blind restore over later writes), and redacted
history refuses (restoring "" would look like an undo while destroying
the document; C3's audit skeleton is deliberately not restorable).

Host/operator surface only — never a dispatch command, never registered
on an agent, never served over MCP: hosts render the undo button, models
do not erase their own trail.
"""

from __future__ import annotations

import dataclasses

from neosian._foundation.memory.dispatch import corrective, dispatch
from neosian._foundation.memory.mounts import MemoryConfig, resolve, writable
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryPathInvalidError,
    MemoryStoreError,
)
from neosian._foundation.tools.base import ToolResult

_INDEX_HINT = "List documents with the 'view' command and path '/' first."


async def revert_memory(
    config: MemoryConfig, path: str, *, version: int, actor: str | None = None
) -> ToolResult[str]:
    """Undo the write that produced `version` of the document at `path`.

    Returns the same corrective `ToolResult[str]` shape as every memory
    command, with a `receipt` (`command="revert"`) naming the version row
    the revert itself appended. `actor` is recorded on that row — pass
    the host's audit identity (e.g. ``host:undo``).
    """
    try:
        mount, doc_path = resolve(config, path)
        if not doc_path:
            raise MemoryPathInvalidError(path, "a mount root is not a document")
        writable(mount)
        rows = await config.store.versions(mount.scope, doc_path, limit=2)
        virtual = f"/{mount.mount_path}/{doc_path}"
        if not rows:
            return ToolResult.fail(
                f"No history for {virtual}", system_reminder=_INDEX_HINT
            )
        if rows[0].version != version:
            raise MemoryConflictError(
                mount.scope,
                doc_path,
                "revert_stale",
                expected_version=version,
                actual_version=rows[0].version,
            )
        prior = rows[1] if len(rows) > 1 else None
        # No live document before row N → the inverse is a delete;
        # otherwise row N-1's content comes back.
        restore = prior is not None and prior.action != "deleted"
        if rows[0].redacted or (restore and prior is not None and prior.redacted):
            return ToolResult.fail(
                f"{virtual}'s history is redacted; the prior content was "
                "cleared and cannot be restored",
                system_reminder="Create the document again if it is still needed.",
            )
        if restore and prior is not None:
            result = await dispatch(
                config, "create", {"path": path, "content": prior.content}, actor=actor
            )
            prose = f"Reverted {virtual} to v{prior.version}'s content"
        else:
            result = await dispatch(config, "delete", {"path": path}, actor=actor)
            prose = f"Reverted {virtual}: v{version} undone — the document is removed"
    except MemoryStoreError as exc:
        return corrective(exc)
    if not result.success or result.receipt is None:
        return result
    # The dispatch prose (and create's overwrite reminder — noise on an
    # undo) is rewritten from the receipt; the receipt itself is re-badged.
    receipt = dataclasses.replace(result.receipt, command="revert")
    return ToolResult.ok(f"{prose} (now v{receipt.version})", receipt=receipt)
