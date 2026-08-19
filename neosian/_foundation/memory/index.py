"""Memory index generation and the system-prompt section (DESIGN §8, C7).

The index is a table of contents, injected once per conversation (frozen:
writes land immediately in the store but surface in the prefix at the next
conversation). `view /` in the tool returns the same rendering — one
renderer, no drift between the tool and the injected section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.shared.prompt_assets import get_prompt, render

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.mounts import MemoryConfig, Mount


async def generate_memory_index(store: MemoryStore, mounts: Sequence[Mount]) -> str:
    """Render the index: every mount and its documents, as markdown."""
    blocks: list[str] = []
    for mount in mounts:
        header = f"## /{mount.mount_path}"
        if mount.description:
            header += f" — {mount.description}"
        if mount.read_only:
            header += " (read-only)"
        lines = [header]
        entries = await store.list_documents(mount.scope)
        if not entries:
            lines.append("(empty)")
        for entry in entries:
            marker = " (redacted)" if entry.redacted else ""
            lines.append(f"- /{mount.mount_path}/{entry.path}{marker}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def memory_system_section(config: MemoryConfig) -> str:
    """The full memory section for a system prompt: prompt pack + index.

    The caller appends this once per conversation — the CLI playground in
    N1, `Conversation` in N2.
    """
    index = await generate_memory_index(config.store, config.mounts)
    return render(get_prompt("memory.system_section"), index=index)
