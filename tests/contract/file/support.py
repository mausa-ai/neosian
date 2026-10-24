"""Literal file locations and byte snapshots for corruption probes."""

import json
from pathlib import Path
from typing import Any

MEMORY = Path("user%3Acontract")
DOCUMENT = MEMORY / "documents/notes/fact.md"
VERSIONS = MEMORY / "versions/notes/fact.jsonl"
CONVERSATION = Path("conversations/contract-session")
SCOPE = "user:contract"
SESSION = "contract-session"


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".store.lock"
    }


def append_row(path: Path, change: dict[str, Any] | str) -> int:
    """Append raw text or a modified copy of the first literal fixture row."""
    lines = path.read_text(encoding="utf-8").splitlines()
    text = (
        change
        if isinstance(change, str)
        else json.dumps({**json.loads(lines[0]), **change})
    )
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(text + "\n")
    return len(lines) + 1
