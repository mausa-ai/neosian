"""Memory index generation and the system-prompt section (DESIGN §8, C7).

The index is a table of contents, injected once per conversation (frozen:
writes land immediately in the store but surface in the prefix at the next
conversation). `view /` in the tool returns the same rendering — one
renderer, no drift between the tool and the injected section.

At scale the index pages instead of growing (DESIGN §8, NG): under
`budget_chars` every document keeps its line, byte-identical to the
unbudgeted rendering; over it, the least recently updated documents fold
into per-directory count lines, and when even the folds cannot fit, a
mount collapses to its total. A folded region is re-hydrated with `view`
of the directory — paging, never deletion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from neosian._foundation.shared.prompt_assets import get_prompt, render

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neosian._foundation.memory.base import MemoryStore
    from neosian._foundation.memory.mounts import MemoryConfig, Mount
    from neosian._foundation.memory.types import MemoryEntry

INDEX_BUDGET_CHARS: Final = 8192
"""Default index budget (~2k tokens under the §6 char heuristic)."""


async def generate_memory_index(
    store: MemoryStore,
    mounts: Sequence[Mount],
    *,
    budget_chars: int = INDEX_BUDGET_CHARS,
) -> str:
    """Render the index: every mount and its documents, as markdown.

    Over `budget_chars`, documents are promoted newest-first onto their
    own lines and the rest fold into per-directory counts; promotion
    stops at the first document that no longer fits — a recency cutoff,
    never a knapsack. The floor — every mount collapsed to its total —
    is returned even if it still exceeds the budget.
    """
    listings = [(mount, await store.list_documents(mount.scope)) for mount in mounts]
    full = _render(listings, hot=None)
    if len(full) <= budget_chars:
        return full
    tiered = _tiered(listings, budget_chars)
    return tiered if tiered is not None else _render_floor(listings)


async def memory_system_section(
    config: MemoryConfig, *, budget_chars: int = INDEX_BUDGET_CHARS
) -> str:
    """The full memory section for a system prompt: prompt pack + index.

    The caller appends this once per conversation — the CLI playground in
    N1, `Conversation` in N2.
    """
    index = await generate_memory_index(
        config.store, config.mounts, budget_chars=budget_chars
    )
    return render(get_prompt("memory.system_section"), index=index)


def _header(mount: Mount) -> str:
    header = f"## /{mount.mount_path}"
    if mount.description:
        header += f" — {mount.description}"
    if mount.read_only:
        header += " (read-only)"
    if mount.edit_only:
        header += " (edit-only)"
    return header


def _count(n: int) -> str:
    return f"{n} document" if n == 1 else f"{n} documents"


def _doc_line(mount: Mount, entry: MemoryEntry) -> str:
    marker = " (redacted)" if entry.redacted else ""
    return f"- /{mount.mount_path}/{entry.path}{marker}"


def _fold_line(mount: Mount, segment: str, count: int) -> str:
    return f"- /{mount.mount_path}/{segment}/ ({_count(count)})"


def _loose_line(mount: Mount, count: int) -> str:
    return f"- /{mount.mount_path}/ ({count} more)"


def _tiered(
    listings: Sequence[tuple[Mount, tuple[MemoryEntry, ...]]], budget_chars: int
) -> str | None:
    """The warm tier, or None when even it cannot hold the budget.

    Exact length accounting over the shared line helpers: promoting a
    document adds its line and shrinks (or removes) its fold line, so
    a promotion may reduce the total — promote while it fits or shrinks.
    """
    docs = [
        (idx, entry) for idx, (_, entries) in enumerate(listings) for entry in entries
    ]
    docs.sort(key=lambda item: (item[0], item[1].path))
    docs.sort(key=lambda item: item[1].updated_at, reverse=True)

    folds: list[dict[str, int]] = []
    loose: list[int] = []
    for _, entries in listings:
        segments: dict[str, int] = {}
        remainder = 0
        for entry in entries:
            if "/" in entry.path:
                segment = entry.path.split("/", 1)[0]
                segments[segment] = segments.get(segment, 0) + 1
            else:
                remainder += 1
        folds.append(segments)
        loose.append(remainder)

    hot: set[tuple[int, str]] = set()
    length = len(_render(listings, hot=hot))
    for idx, entry in docs:
        mount = listings[idx][0]
        delta = len(_doc_line(mount, entry)) + 1
        if "/" in entry.path:
            segment = entry.path.split("/", 1)[0]
            count = folds[idx][segment]
            before = len(_fold_line(mount, segment, count))
            if count == 1:
                delta -= before + 1
            else:
                delta -= before - len(_fold_line(mount, segment, count - 1))
        else:
            count = loose[idx]
            before = len(_loose_line(mount, count))
            if count == 1:
                delta -= before + 1
            else:
                delta -= before - len(_loose_line(mount, count - 1))
        if length + delta > budget_chars and delta >= 0:
            break
        length += delta
        hot.add((idx, entry.path))
        if "/" in entry.path:
            folds[idx][segment] = count - 1
        else:
            loose[idx] = count - 1
    if length > budget_chars:
        return None
    return _render(listings, hot=hot)


def _render(
    listings: Sequence[tuple[Mount, tuple[MemoryEntry, ...]]],
    hot: set[tuple[int, str]] | None,
) -> str:
    """One block per mount: hot document lines, then per-directory folds.

    `hot=None` keeps every document line — the exact pre-budget rendering.
    """
    blocks: list[str] = []
    for idx, (mount, entries) in enumerate(listings):
        lines = [_header(mount)]
        if not entries:
            lines.append("(empty)")
        segments: dict[str, int] = {}
        remainder = 0
        for entry in entries:
            if hot is None or (idx, entry.path) in hot:
                lines.append(_doc_line(mount, entry))
            elif "/" in entry.path:
                segment = entry.path.split("/", 1)[0]
                segments[segment] = segments.get(segment, 0) + 1
            else:
                remainder += 1
        for segment in sorted(segments):
            lines.append(_fold_line(mount, segment, segments[segment]))
        if remainder:
            lines.append(_loose_line(mount, remainder))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_floor(
    listings: Sequence[tuple[Mount, tuple[MemoryEntry, ...]]],
) -> str:
    """The cold tier: every mount collapsed to its document total."""
    blocks: list[str] = []
    for mount, entries in listings:
        lines = [_header(mount)]
        if not entries:
            lines.append("(empty)")
        else:
            lines.append(
                f"({_count(len(entries))} — view /{mount.mount_path}/ to list)"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
