"""The store-mobility fixture shared by every substrate pair (NC4, §26.5):
a rich store on the source, then equality of every ABC read on both
sides — the done-when, "contract-kit-indistinguishable"."""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.conversation.types import ConversationProjection
from neosian._foundation.llm.base import ImageBlock, Message, Role, TextBlock, ToolCall
from neosian._foundation.memory.paths import path_segments
from neosian._foundation.memory.scope import parse_scope, scope_directory
from neosian._foundation.shared.types import ToolCallId, ToolName

if TYPE_CHECKING:
    from pathlib import Path

    from neosian._foundation.conversation.base import ConversationStore
    from neosian._foundation.memory.base import MemoryStore

SCOPES = ("user:kit", "user:kit/proj:p")
CONVERSATIONS = ("conv-a", "conv-b")
DELETED = ("c",)  # renamed away — reads None on both sides

_TURNS = (
    (
        Message(role=Role.USER, content="plain"),
        Message(role=Role.ASSISTANT, content="re"),
    ),
    (
        Message(role=Role.USER, content="call tools"),
        Message(
            role=Role.ASSISTANT,
            content=None,
            tool_calls=[
                ToolCall(
                    id=ToolCallId("call-1"),
                    name=ToolName("lookup"),
                    arguments={"query": "x", "options": {"depth": 2}},
                )
            ],
        ),
        Message(role=Role.TOOL, content="result", tool_call_id=ToolCallId("call-1")),
        Message(role=Role.ASSISTANT, content="done"),
    ),
    (
        Message(
            role=Role.USER,
            content=[
                TextBlock(text="---\ncafé ✓\r\n"),
                ImageBlock(url="https://x.test/i"),
            ],
        ),
        Message(role=Role.ASSISTANT, content=None, reasoning="chain of thought"),
    ),
)
_PROJECTIONS = (
    ConversationProjection(turn=1, kind="log", text="USER plain"),
    ConversationProjection(turn=2, kind="digest", text="tools ran"),
    ConversationProjection(turn=2, kind="log", text="a second entry on turn 2"),
    ConversationProjection(turn=3, kind="epoch", text="the whole span", span=3),
)
_CONTENTS = ("", "---\nnot frontmatter\n", "a\r\nb", "café ✓ — 日本語")


async def seed(
    store: MemoryStore | ConversationStore, *, plant_root: Path | None
) -> None:
    """Creates, edits, a delete and re-create, a rename, both redaction
    shapes, a planted unknown frontmatter key (files only), turns with
    every message shape and projections of every kind."""
    memory: MemoryStore = store  # type: ignore[assignment]
    for scope in SCOPES:
        for index, content in enumerate(_CONTENTS):
            await memory.write(scope, "notes/a", content, actor=f"w{index}")
        await memory.write(scope, "b", "first life", actor="conv:x#1")
        await memory.delete(scope, "b", actor="conv:x#2")
        await memory.write(scope, "b", "second life")
        await memory.write(scope, "c", "to be renamed", actor="cli:local")
        await memory.rename(scope, "c", "d", actor="cli:local")
        await memory.write(scope, "e", "secret", actor="mcp:stdio")
        await memory.redact(scope, path="e", actor="ops")
        await memory.write(scope, "e", "after the erasure")
    if plant_root is not None:
        _plant(plant_root, SCOPES[0], "planted/x", "planted body")
        await memory.write(SCOPES[0], "planted/x", "edited after planting")
    await memory.redact(SCOPES[1], actor="ops")
    turns: ConversationStore = store  # type: ignore[assignment]
    for index, messages in enumerate(_TURNS):
        await turns.append_turn(
            CONVERSATIONS[0],
            messages,
            actor=None if index == 1 else f"claude-code:s{index}",
        )
    await turns.append_projections(CONVERSATIONS[0], _PROJECTIONS)
    await turns.append_projections(CONVERSATIONS[1], _PROJECTIONS[:1])


async def assert_indistinguishable(a: object, b: object) -> None:
    """Every ABC read answers the same on both stores."""
    ma: MemoryStore = a  # type: ignore[assignment]
    mb: MemoryStore = b  # type: ignore[assignment]
    for scope in SCOPES:
        assert await ma.list_documents(scope) == await mb.list_documents(scope)
        history = await ma.history(scope)
        assert history == await mb.history(scope)
        assert history, scope
        for path in {row.path for row in history} | set(DELETED):
            assert await ma.read(scope, path) == await mb.read(scope, path), path
            assert await ma.versions(scope, path, limit=10_000) == await mb.versions(
                scope, path, limit=10_000
            ), path
        assert await ma.redactions(scope) == await mb.redactions(scope)
    ca: ConversationStore = a  # type: ignore[assignment]
    cb: ConversationStore = b  # type: ignore[assignment]
    for conversation_id in CONVERSATIONS:
        assert await ca.read_turns(conversation_id) == await cb.read_turns(
            conversation_id
        )
        assert await ca.read_projections(conversation_id) == await cb.read_projections(
            conversation_id
        )
        assert await ca.last_turn_number(conversation_id) == await cb.last_turn_number(
            conversation_id
        )


async def assert_numbering_continues(target: object) -> None:
    """A restored store keeps counting where the original left off."""
    memory: MemoryStore = target  # type: ignore[assignment]
    before = (await memory.versions(SCOPES[0], "b", limit=1))[0].version
    assert (await memory.write(SCOPES[0], "b", "third life")).version == before + 1
    turns: ConversationStore = target  # type: ignore[assignment]
    appended = await turns.append_turn(CONVERSATIONS[0], _TURNS[0])
    assert appended.turn == len(_TURNS) + 1


def _plant(root: Path, scope: str, path: str, content: str) -> None:
    segments = path_segments(path)
    doc_file = root.joinpath(
        *scope_directory(parse_scope(scope)),
        "documents",
        *segments[:-1],
        segments[-1] + ".md",
    )
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text(
        "---\nneosian_format: 1\nversion: 1\ncreated_at: 2026-08-19T09:00:00Z\n"
        f"updated_at: 2026-08-19T09:00:00Z\nowner: kit\n---\n{content}",
        encoding="utf-8",
        newline="",
    )
