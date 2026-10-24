"""Literal restore records, occupied targets and ordering under equal timestamps."""

from __future__ import annotations

import pytest

from tests.contract.storage.support import Storage, refused
from tests.contract.support import fixture


def test_scope_restore_keeps_metadata_actors_versions_and_erasures(
    storage: Storage,
) -> None:
    storage.require("portable")
    data = fixture("records.json")
    document = {**data["document"], "scope": storage.scope}
    body = {
        "scope": storage.scope,
        "documents": [document],
        "versions": data["versions"],
        "redactions": [data["redaction"]],
    }
    assert storage.server.post("store/restore_scope", **body) == {}
    assert storage.scope in storage.server.post("store/scopes")["scopes"]
    assert storage.memory("read", path="notes/fact")["document"] == document
    assert storage.memory("versions", path="notes/fact")["versions"] == list(
        reversed(data["versions"][:2])
    )
    assert storage.memory("redactions")["redactions"] == [data["redaction"]]
    assert (
        refused(
            storage.server.request("/v1/store/restore_scope", body), "memory_conflict"
        )["reason"]
        == "target_occupied"
    )
    changed = storage.memory("write", path="notes/fact", content="new")["document"]
    assert changed["extra"] == document["extra"]
    assert changed["version"] == 3 and changed["created_at"] == document["created_at"]


@pytest.mark.parametrize("occupied", ["history", "trail"])
def test_scope_history_or_trail_alone_prevents_restore(
    storage: Storage, occupied: str
) -> None:
    storage.require("portable")
    data = fixture("records.json")
    storage.server.post(
        "store/restore_scope",
        scope=storage.scope,
        documents=[],
        versions=data["versions"] if occupied == "history" else [],
        redactions=[data["redaction"]] if occupied == "trail" else [],
    )
    refused(
        storage.server.request(
            "/v1/store/restore_scope",
            {"scope": storage.scope, "documents": [], "versions": [], "redactions": []},
        ),
        "memory_conflict",
    )


def test_conversation_restore_preserves_erasure_and_projection_rows(
    storage: Storage,
) -> None:
    storage.require("portable")
    storage.require("erasable")
    data = fixture("records.json")
    turns = [
        {**turn, "conversation_id": storage.conversation} for turn in data["turns"]
    ]
    trail = [{**data["turn_redaction"], "conversation_id": storage.conversation}]
    body = {
        "conversation_id": storage.conversation,
        "turns": turns,
        "projections": data["projections"],
        "redactions": trail,
    }
    storage.server.post("store/restore_conversation", **body)
    assert (
        storage.conversation
        in storage.server.post("store/conversations")["conversations"]
    )
    assert storage.turns("read_turns")["turns"] == turns
    assert storage.turns("read_projections")["entries"] == data["projections"]
    assert (
        storage.server.post(
            "conversation/turn_redactions", conversations=[storage.conversation]
        )["redactions"]
        == trail
    )
    assert (
        refused(
            storage.server.request("/v1/store/restore_conversation", body),
            "agent_conversation_conflict",
        )["reason"]
        == "target_occupied"
    )


def test_projection_only_conversation_prevents_restore(storage: Storage) -> None:
    storage.require("portable")
    storage.turns(
        "append_projections", entries=[{"turn": 8, "kind": "log", "text": "keep"}]
    )
    refused(
        storage.server.request(
            "/v1/store/restore_conversation",
            {"conversation_id": storage.conversation, "turns": [], "projections": []},
        ),
        "agent_conversation_conflict",
    )
    assert storage.turns("read_projections")["entries"][0]["text"] == "keep"


def test_search_ties_use_conversation_then_turn_descending(storage: Storage) -> None:
    storage.require("portable")
    template = fixture("records.json")["turns"][0]
    ids = [storage.conversation + suffix for suffix in ["-a", "-b"]]
    for name in ids:
        turns = [{**template, "conversation_id": name, "turn": n} for n in [1, 2]]
        storage.server.post(
            "store/restore_conversation",
            conversation_id=name,
            turns=turns,
            projections=[],
        )
    hits = storage.server.post(
        "conversation/search_turns", query="café", conversations=ids
    )["turns"]
    assert [(hit["conversation_id"], hit["turn"]) for hit in hits] == [
        (ids[1], 2),
        (ids[1], 1),
        (ids[0], 2),
        (ids[0], 1),
    ]
