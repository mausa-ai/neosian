"""The structured receipt a mutating memory command returns (NP).

`MemoryWriteReceipt` is the machine-readable twin of the prose in
`ToolResult.data`: the command bodies (`commands.py`) are the one place
that simultaneously know mount, virtual path, command and store-assigned
version, and they record them here. The receipt rides
`ToolResult.receipt` — an in-process seam `to_json()` deliberately
ignores, so the wire envelope every transport prints is byte-identical
with or without it. Out-of-process transports (MCP, the CLI's JSON
envelope) therefore drop the receipt by construction; its consumers are
in-process: the agent loop's `memory_write` event, reflection's and
maintenance's result rows, and `revert_memory`'s rewritten prose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryWriteReceipt:
    """One successful mutating memory command, structurally.

    Attributes:
        command: The dispatcher command that ran (``create``,
            ``str_replace``, ``insert``, ``delete``, ``rename``) or
            ``revert``.
        mount_path: The mount's top-level segment (no slashes).
        path: The canonical virtual path (``/mount/doc``); for ``rename``,
            the destination.
        version: The version row the command appended — for ``delete``,
            the row the deletion consumed. The argument an undo passes
            back to `revert_memory`.
        previous_path: ``rename`` only: the source virtual path.
    """

    command: str
    mount_path: str
    path: str
    version: int
    previous_path: str | None = None
