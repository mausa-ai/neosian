"""The write-path payload — every writable mount's live bodies, fenced
and budgeted.

Reflection (§15) and maintenance (§16) show the model the current memory
so it updates what it can see instead of duplicating it. The bodies are
untrusted data — a stored document may carry text shaped like a heading
or an instruction — so each rides between fence lines tagged with a
per-call token the system prompt names, and the payload is bounded: past
`PAYLOAD_BUDGET_CHARS` the remaining documents are listed by name only,
so the dedup evidence survives where the bodies cannot, and the session
transcript takes what the documents left, oldest turns dropped first
with their count named. Bodies stay raw inside their fences, so an
emitted `old_str` still matches stored content exactly.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from neosian._foundation.memory.mounts import MemoryConfig
    from neosian._foundation.memory.types import MemoryEntry

# ~30k tokens at the classic four characters per token — inside every
# shipped model's window with the transcript and the reply to spare.
PAYLOAD_BUDGET_CHARS: Final = 120_000

OMITTED_LINE: Final = "[body omitted — over the payload budget]"

_TRANSCRIPT_HEADING: Final = "# Session transcript\n\n"


def new_fence() -> str:
    """A per-call token: the system prompt names it, the bodies wear it."""
    return secrets.token_hex(8)


def fenced(fence: str, text: str) -> str:
    return f"<<<data {fence}>>>\n{text}\n<<<end {fence}>>>"


async def render_documents(
    config: MemoryConfig,
    *,
    fence: str,
    edit_only_note: str,
    annotate: Callable[[MemoryEntry], str] | None = None,
    budget: int = PAYLOAD_BUDGET_CHARS,
) -> str:
    """`# Current memory`: every writable mount with its documents' fenced
    raw bodies. Read-only mounts take no operations and are not shown;
    redacted documents are named, never read; edit-only mounts are shown
    under `edit_only_note`; `annotate(entry)` is appended to a document's
    header (the gardener's aging evidence)."""
    lines = ["# Current memory"]
    used = len(lines[0]) + 1
    folded = False

    def add(line: str) -> None:
        nonlocal used
        lines.append(line)
        used += len(line) + 1

    for mount in config.mounts:
        if mount.read_only:
            continue
        header = f"\n## /{mount.mount_path}"
        if mount.description:
            header += f" — {mount.description}"
        if mount.edit_only:
            header += f" ({edit_only_note})"
        add(header)
        entries = await config.store.list_documents(mount.scope)
        if not entries:
            add("(empty)")
        for entry in entries:
            name = f"### /{mount.mount_path}/{entry.path}"
            if entry.redacted:
                add(f"{name} (redacted — protected, take no action)")
                continue
            document = await config.store.read(mount.scope, entry.path)
            if document is None:
                continue
            add(name + (annotate(entry) if annotate is not None else ""))
            body = fenced(fence, document.content)
            folded = folded or used + len(body) + 1 > budget
            add(OMITTED_LINE if folded else body)
    return "\n".join(lines)


def _omitted_turns(count: int) -> str:
    noun = "turn" if count == 1 else "turns"
    return f"[{count} earlier {noun} omitted — over the payload budget]"


def render_transcript(turns: Sequence[str], *, fence: str, budget: int) -> str:
    """`# Session transcript`: the newest rendered turns that fit `budget`,
    fenced; when older turns were dropped, the first line names how many."""
    used = len(_TRANSCRIPT_HEADING) + len(fenced(fence, "")) + 2
    used += len(_omitted_turns(len(turns)))
    kept: list[str] = []
    for turn in reversed(turns):
        if used + len(turn) + 2 > budget:
            break
        kept.append(turn)
        used += len(turn) + 2
    kept.reverse()
    if len(kept) < len(turns):
        kept.insert(0, _omitted_turns(len(turns) - len(kept)))
    return _TRANSCRIPT_HEADING + fenced(fence, "\n\n".join(kept))
