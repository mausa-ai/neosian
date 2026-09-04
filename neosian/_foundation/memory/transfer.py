"""`transfer` — a store moves whole, any substrate to any other (§26).

Export is privilege-free: `archive_scope` / `archive_conversation` are
compositions over the ABC reads. Only the target needs `Portable` (and
the source only when it must enumerate). The archive directory is a
FileStore root (ledger #164), so export = `transfer(store,
FileStore(dir))`, import = `transfer(FileStore(dir), store)`, and a
direct store-to-store move is the same call.

Semantics (ledger #165): the unit is one scope or one conversation; a
preflight over the target refuses the whole run before anything is
written if any unit is occupied; then units land one by one, each
atomic where its substrate can. A race between preflight and restore
surfaces as the same conflict mid-run with the earlier units standing —
re-run naming the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.portable import (
    ConversationArchive,
    Portable,
    ScopeArchive,
    TransferReport,
    UnitReport,
)
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.shared.exceptions import (
    ConversationConflictError,
    MemoryConflictError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


async def archive_scope(store: MemoryStore, scope: str) -> ScopeArchive:
    """One scope over the ABC reads: live documents (with `extra`),
    every version row ever written, the erasure trail."""
    entries = await store.list_documents(scope)
    documents = [await store.read(scope, entry.path) for entry in entries]
    rows = await store.history(scope)
    return ScopeArchive(
        scope=scope,
        documents=tuple(document for document in documents if document is not None),
        versions=tuple(sorted(rows, key=lambda row: (row.path, row.version))),
        redactions=tuple(reversed(await store.redactions(scope))),
    )


async def archive_conversation(
    store: ConversationStore, conversation_id: str
) -> ConversationArchive:
    return ConversationArchive(
        conversation_id=conversation_id,
        turns=await store.read_turns(conversation_id),
        projections=await store.read_projections(conversation_id),
    )


async def transfer(
    source: object,
    target: object,
    *,
    scopes: Iterable[str] | None = None,
    conversations: Iterable[str] | None = None,
) -> TransferReport:
    """Move units from `source` into `target`, verbatim (§26.3).

    Naming either `scopes` or `conversations` moves only the named units
    (`scopes=` alone moves no conversation); naming neither moves every
    unit the source enumerates. Names are validated before any I/O.
    """
    if not isinstance(target, Portable):
        raise TypeError(_refusal(target, "cannot be restored into"))
    if (scopes is None or conversations is None) and not isinstance(source, Portable):
        raise TypeError(_refusal(source, "cannot enumerate its units"))
    narrowed = scopes is not None or conversations is not None
    scope_names = await _names(source, scopes, narrowed, "scopes")
    conversation_names = await _names(source, conversations, narrowed, "conversations")
    memory, turns = _seams(source, scope_names, conversation_names)
    await _preflight(target, scope_names, conversation_names)
    units: list[UnitReport] = []
    for scope in scope_names:
        archive = await archive_scope(memory, scope)
        await target.restore_scope(archive)
        units.append(
            UnitReport(
                "scope",
                scope,
                documents=len(archive.documents),
                versions=len(archive.versions),
                redactions=len(archive.redactions),
            )
        )
    for conversation_id in conversation_names:
        record = await archive_conversation(turns, conversation_id)
        await target.restore_conversation(record)
        units.append(
            UnitReport(
                "conversation",
                conversation_id,
                turns=len(record.turns),
                projections=len(record.projections),
            )
        )
    return TransferReport(tuple(units))


def _refusal(store: object, what: str) -> str:
    return (
        f"{type(store).__name__} does not implement Portable and {what} — "
        "FileStore, PostgresStore and RemoteStore do (DESIGN §26.1)"
    )


async def _names(
    source: object, given: Iterable[str] | None, narrowed: bool, kind: str
) -> tuple[str, ...]:
    validate = parse_scope if kind == "scopes" else parse_conversation_id
    if given is not None:
        return tuple(sorted({validate(name) for name in given}))
    if narrowed:
        return ()
    assert isinstance(source, Portable)  # checked by the caller
    listed = await (source.scopes() if kind == "scopes" else source.conversations())
    return tuple(sorted(listed))


def _seams(
    source: object, scopes: tuple[str, ...], conversations: tuple[str, ...]
) -> tuple[MemoryStore, ConversationStore]:
    if scopes and not isinstance(source, MemoryStore):
        raise TypeError(f"{type(source).__name__} is not a MemoryStore")
    if conversations and not isinstance(source, ConversationStore):
        raise TypeError(f"{type(source).__name__} is not a ConversationStore")
    # A store that carries none of a seam's units is never asked for it.
    return source, source  # type: ignore[return-value]


async def _preflight(
    target: object, scopes: tuple[str, ...], conversations: tuple[str, ...]
) -> None:
    """Reads only: refuse the whole run before the first write."""
    if scopes:
        assert isinstance(target, MemoryStore)
        for scope in scopes:
            if (
                await target.list_documents(scope)
                or await target.history(scope, limit=1)
                or await target.redactions(scope, limit=1)
            ):
                raise MemoryConflictError(scope, None, "target_occupied")
    if conversations:
        assert isinstance(target, ConversationStore)
        for conversation_id in conversations:
            if await target.last_turn_number(
                conversation_id
            ) or await target.read_projections(conversation_id, limit=1):
                raise ConversationConflictError(conversation_id, "target_occupied")
