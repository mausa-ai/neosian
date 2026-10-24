"""Containment and parse failures precede destructive filesystem changes."""

import shutil

import pytest

from tests.contract.file.support import (
    CONVERSATION,
    DOCUMENT,
    MEMORY,
    SCOPE,
    SESSION,
    snapshot,
)
from tests.contract.file.test_layout import symlink
from tests.contract.storage.support import refused
from tests.contract.support import Binary, fixture, serve


@pytest.mark.parametrize("name", ["projections.jsonl", "redactions.jsonl"])
def test_unreadable_related_log_prevents_turn_erasure(
    planted: Binary, name: str
) -> None:
    with serve(planted) as server:
        (planted.home / CONVERSATION / name).write_text("{broken\n")
        before = snapshot(planted.home)
        refused(
            server.request(
                "/v1/conversation/redact_turns", {"conversation_id": SESSION}
            ),
            "agent_conversation_format_unsupported",
        )
        assert snapshot(planted.home) == before


@pytest.mark.parametrize("whole_scope", [False, True])
def test_unreadable_document_prevents_every_memory_erasure_write(
    planted: Binary, whole_scope: bool
) -> None:
    with serve(planted) as server:
        (planted.home / DOCUMENT).write_text("broken")
        before = snapshot(planted.home)
        body = (
            {"scope": SCOPE} if whole_scope else {"scope": SCOPE, "path": "notes/fact"}
        )
        refused(server.request("/v1/memory/redact", body), "memory_format_unsupported")
        assert snapshot(planted.home) == before


@pytest.mark.parametrize(
    "name,route",
    [
        ("turns.jsonl", "read_turns"),
        ("projections.jsonl", "read_projections"),
        ("redactions.jsonl", "turn_redactions"),
    ],
)
def test_conversation_file_symlinks_refuse_reads_and_erasure(
    planted: Binary, name: str, route: str
) -> None:
    outside = planted.directory / "outside"
    shutil.copytree(planted.home / CONVERSATION, outside)
    before = snapshot(outside)
    with serve(planted) as server:
        link = planted.home / CONVERSATION / name
        link.unlink()
        symlink(link, outside / name)
        body = (
            {"conversations": [SESSION]}
            if route == "turn_redactions"
            else {"conversation_id": SESSION}
        )
        refused(
            server.request("/v1/conversation/" + route, body),
            "agent_conversation_id_invalid",
        )
        refused(
            server.request(
                "/v1/conversation/redact_turns", {"conversation_id": SESSION}
            ),
            "agent_conversation_id_invalid",
        )
        assert snapshot(outside) == before


def test_search_erasure_and_restore_cannot_follow_a_conversation_directory(
    planted: Binary,
) -> None:
    outside = planted.directory / "outside"
    shutil.copytree(planted.home / CONVERSATION, outside)
    before = snapshot(outside)
    with serve(planted) as server:
        symlink(planted.home / "conversations/escape", outside)
        for route, body in [
            (
                "conversation/search_turns",
                {"query": "review", "conversations": ["escape"]},
            ),
            ("conversation/redact_turns", {"conversation_id": "escape"}),
            ("conversation/turn_redactions", {"conversations": ["escape"]}),
            (
                "store/restore_conversation",
                {"conversation_id": "escape", "turns": [], "projections": []},
            ),
        ]:
            refused(
                server.request("/v1/" + route, body), "agent_conversation_id_invalid"
            )
            assert snapshot(outside) == before
        assert server.post("store/conversations")["conversations"] == [SESSION]
        assert (
            len(server.post("conversation/search_turns", query="review")["turns"]) == 1
        )


def test_memory_trail_and_listing_do_not_follow_file_symlinks(planted: Binary) -> None:
    outside = planted.directory / "outside"
    shutil.copytree(planted.home / MEMORY, outside)
    before = snapshot(outside)
    with serve(planted) as server:
        link = planted.home / MEMORY / "redactions.jsonl"
        link.unlink()
        symlink(link, outside / "redactions.jsonl")
        for route in ["redactions", "redact"]:
            refused(
                server.request("/v1/memory/" + route, {"scope": SCOPE}),
                "memory_path_invalid",
            )
        symlink(
            planted.home / MEMORY / "documents/escape.md",
            outside / "documents/notes/fact.md",
        )
        refused(
            server.request("/v1/memory/list_documents", {"scope": SCOPE}),
            "memory_path_invalid",
        )
        assert snapshot(outside) == before


def test_restore_checks_all_paths_before_writing_the_first(planted: Binary) -> None:
    outside = planted.directory / "outside"
    outside.mkdir()
    destination = planted.home / "user%3Arestored"
    (destination / "documents").mkdir(parents=True)
    symlink(destination / "documents/escape", outside)
    data = fixture("records.json")
    documents = [
        {**data["document"], "scope": "user:restored", "path": path}
        for path in ["first", "escape/last"]
    ]
    before = snapshot(planted.home)
    with serve(planted) as server:
        refused(
            server.request(
                "/v1/store/restore_scope",
                {
                    "scope": "user:restored",
                    "documents": documents,
                    "versions": [],
                    "redactions": [],
                },
            ),
            "memory_path_invalid",
        )
        assert snapshot(planted.home) == before
        assert list(outside.iterdir()) == []
