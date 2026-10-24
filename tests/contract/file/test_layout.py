"""Private physical writes, containment and enumeration without product helpers."""

import shutil
import stat
import sys
from pathlib import Path

import pytest

from tests.contract.file.support import (
    CONVERSATION,
    DOCUMENT,
    MEMORY,
    SCOPE,
    SESSION,
    VERSIONS,
    snapshot,
)
from tests.contract.storage.support import message, refused
from tests.contract.support import Binary, fixture, serve


def symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        if sys.platform == "win32":
            pytest.skip("symlink creation requires Windows privileges")
        raise


def test_reads_and_empty_restores_do_not_create_state(binary: Binary) -> None:
    with serve(binary) as server:
        assert server.post("memory/read", scope="user:ghost", path="doc") == {
            "document": None
        }
        assert server.post("memory/list_documents", scope="user:ghost")["entries"] == []
        assert server.post("memory/redact", scope="user:ghost") == {"count": 0}
        assert (
            server.post("conversation/read_turns", conversation_id="ghost")["turns"]
            == []
        )
        assert server.post("conversation/redact_turns", conversation_id="ghost") == {
            "count": 0
        }
        server.post(
            "store/restore_scope",
            scope="user:ghost",
            documents=[],
            versions=[],
            redactions=[],
        )
        server.post(
            "store/restore_conversation",
            conversation_id="ghost",
            turns=[],
            projections=[],
        )
        assert sorted(path.name for path in binary.home.iterdir()) == [".store.lock"]


def test_mutation_erasure_and_restore_files_are_private(binary: Binary) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX file permission bits")
    with serve(binary) as server:
        for content in ["secret", "revised"]:
            server.post("memory/write", scope=SCOPE, path="notes/doc", content=content)
        server.post("memory/redact", scope=SCOPE, path="notes/doc")
        server.post(
            "conversation/append_turn",
            conversation_id=SESSION,
            messages=[message("secret")],
        )
        server.post(
            "conversation/append_projections",
            conversation_id=SESSION,
            entries=[{"turn": 1, "kind": "log", "text": "secret"}],
        )
        server.post("conversation/redact_turns", conversation_id=SESSION)
        data = fixture("records.json")
        server.post(
            "store/restore_scope",
            scope="user:restored",
            documents=[{**data["document"], "scope": "user:restored"}],
            versions=data["versions"],
            redactions=[data["redaction"]],
        )
        for path in [binary.home, *binary.home.rglob("*")]:
            assert stat.S_IMODE(path.stat().st_mode) == (
                0o700 if path.is_dir() else 0o600
            ), path
        assert list(binary.home.rglob(".neosian-tmp-*")) == []
        assert "secret" not in (binary.home / CONVERSATION / "turns.jsonl").read_text()
        assert (
            "secret"
            not in (binary.home / CONVERSATION / "projections.jsonl").read_text()
        )


@pytest.mark.parametrize("location", ["documents", "versions"])
def test_memory_directory_symlink_cannot_escape_the_store(
    planted: Binary, location: str
) -> None:
    outside = planted.directory / "outside"
    outside.mkdir()
    source = DOCUMENT if location == "documents" else VERSIONS
    target = outside / ("fact.md" if location == "documents" else "fact.jsonl")
    shutil.copyfile(planted.home / source, target)
    before = snapshot(outside)
    with serve(planted) as server:
        symlink(planted.home / MEMORY / location / "escape", outside)
        routes = (
            ["read", "write", "delete", "redact"]
            if location == "documents"
            else ["versions", "write", "redact"]
        )
        for route in routes:
            details = refused(
                server.request(
                    "/v1/memory/" + route,
                    {"scope": SCOPE, "path": "escape/fact", "content": "must not land"},
                ),
                "memory_path_invalid",
            )
            assert "escapes" in details["reason"]
            assert snapshot(outside) == before


def test_memory_file_symlink_cannot_escape_the_store(planted: Binary) -> None:
    outside = planted.directory / "outside.md"
    shutil.copyfile(planted.home / DOCUMENT, outside)
    before = outside.read_bytes()
    with serve(planted) as server:
        symlink(planted.home / MEMORY / "documents/escape.md", outside)
        for route in ["read", "write", "delete", "redact"]:
            refused(
                server.request(
                    "/v1/memory/" + route,
                    {"scope": SCOPE, "path": "escape", "content": "must not land"},
                ),
                "memory_path_invalid",
            )
            assert outside.read_bytes() == before


def test_conversation_read_and_append_refuse_an_escaping_directory(
    planted: Binary,
) -> None:
    outside = planted.directory / "outside"
    shutil.copytree(planted.home / CONVERSATION, outside)
    before = snapshot(outside)
    with serve(planted) as server:
        symlink(planted.home / "conversations/escape", outside)
        for route in [
            "read_turns",
            "read_projections",
            "last_turn_number",
            "append_turn",
        ]:
            refused(
                server.request(
                    "/v1/conversation/" + route,
                    {
                        "conversation_id": "escape",
                        "messages": [message("must not land")],
                    },
                ),
                "agent_conversation_id_invalid",
            )
            assert snapshot(outside) == before


def test_foreign_names_are_skipped_and_history_only_scopes_are_enumerated(
    planted: Binary,
) -> None:
    with serve(planted) as server:
        (planted.home / MEMORY / "documents/My Notes.md").write_text("foreign")
        foreign = planted.home / "conversations/a b"
        foreign.mkdir()
        (foreign / "turns.jsonl").write_text("{broken review\n")
        (foreign / "redactions.jsonl").write_text("{broken\n")
        (planted.home / "conversations/empty").mkdir()
        for scope in ["user:a", "user:a/proj:p", "user:gone"]:
            server.post("memory/write", scope=scope, path="doc", content="one")
        server.post("memory/delete", scope="user:gone", path="doc")
        assert server.post("store/scopes")["scopes"] == [
            "user:a",
            "user:a/proj:p",
            SCOPE,
            "user:gone",
        ]
        assert server.post("store/conversations")["conversations"] == [SESSION]
        assert len(server.post("memory/list_documents", scope=SCOPE)["entries"]) == 2
        assert (
            len(server.post("conversation/search_turns", query="review")["turns"]) == 1
        )
        assert len(server.post("conversation/turn_redactions")["redactions"]) == 1


def test_valid_document_name_with_a_broken_envelope_is_not_foreign(
    planted: Binary,
) -> None:
    with serve(planted) as server:
        (planted.home / MEMORY / "documents/broken.md").write_text("no envelope")
        refused(
            server.request("/v1/memory/list_documents", {"scope": SCOPE}),
            "memory_format_unsupported",
        )
