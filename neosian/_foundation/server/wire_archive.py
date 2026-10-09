"""The archive halves of the wire codec (NC4, §26): a `ScopeArchive` and
a `ConversationArchive` as flat JSON over the row codecs in `wire.py`,
typed at the door like every other parameter. A conversation's
`redactions` (N8, §38) is optional on the way in: an archive a 5 client
wrote carries none."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neosian._foundation.memory.portable import ConversationArchive, ScopeArchive
from neosian._foundation.server.wire import (
    decode_document,
    decode_projection,
    decode_redaction,
    decode_turn,
    decode_turn_redaction,
    decode_version,
    encode_document,
    encode_projection,
    encode_redaction,
    encode_turn,
    encode_turn_redaction,
    encode_version,
    optional_objects,
    require_objects,
    require_str,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def encode_scope_archive(archive: ScopeArchive) -> dict[str, Any]:
    return {
        "scope": archive.scope,
        "documents": [encode_document(d) for d in archive.documents],
        "versions": [encode_version(row) for row in archive.versions],
        "redactions": [encode_redaction(act) for act in archive.redactions],
    }


def decode_scope_archive(payload: Mapping[str, Any]) -> ScopeArchive:
    return ScopeArchive(
        scope=require_str(payload, "scope"),
        documents=tuple(
            decode_document(d) for d in require_objects(payload, "documents")
        ),
        versions=tuple(decode_version(r) for r in require_objects(payload, "versions")),
        redactions=tuple(
            decode_redaction(a) for a in require_objects(payload, "redactions")
        ),
    )


def encode_conversation_archive(archive: ConversationArchive) -> dict[str, Any]:
    return {
        "conversation_id": archive.conversation_id,
        "turns": [encode_turn(turn) for turn in archive.turns],
        "projections": [encode_projection(entry) for entry in archive.projections],
        "redactions": [encode_turn_redaction(act) for act in archive.redactions],
    }


def decode_conversation_archive(payload: Mapping[str, Any]) -> ConversationArchive:
    return ConversationArchive(
        conversation_id=require_str(payload, "conversation_id"),
        turns=tuple(decode_turn(t) for t in require_objects(payload, "turns")),
        projections=tuple(
            decode_projection(p) for p in require_objects(payload, "projections")
        ),
        redactions=tuple(
            decode_turn_redaction(a) for a in optional_objects(payload, "redactions")
        ),
    )
