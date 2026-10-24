"""Literal format-1 files and records remain readable without the old SDK."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from typing import Any

import pytest
import yaml
from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    ValidationError,
)

from tests.contract.schema import OPENAPI, STATE, validate
from tests.contract.support import FIXTURES, Binary, fixture, serve


def test_schema_and_literal_records() -> None:
    Draft202012Validator.check_schema(STATE)
    data = fixture("records.json")
    for kind, value in [
        ("Document", data["document"]),
        ("MemoryRedaction", data["redaction"]),
        ("TurnRedaction", data["turn_redaction"]),
        ("Mailbox", data["mailbox"]),
        *(("Version", row) for row in data["versions"]),
        *(("Turn", row) for row in data["turns"]),
        *(("Projection", row) for row in data["projections"]),
    ]:
        validate(kind, value)


def test_literal_file_rows_match_the_published_definitions() -> None:
    home = FIXTURES / "home"
    for path in home.rglob("*.md"):
        _, header, _ = path.read_text(encoding="utf-8").split("---\n", 2)
        validate("MemoryEnvelope", yaml.safe_load(header))
    for path in home.rglob("*.jsonl"):
        if "conversations" in path.parts:
            kind = {
                "turns.jsonl": "TurnRow",
                "projections.jsonl": "ProjectionRow",
                "redactions.jsonl": "TurnRedactionRow",
            }[path.name]
        else:
            kind = (
                "MemoryRedactionRow"
                if path.name == "redactions.jsonl"
                else "VersionRow"
            )
        for line in path.read_text(encoding="utf-8").splitlines():
            validate(kind, json.loads(line))


@pytest.mark.parametrize(
    ("kind", "key", "bad"),
    [
        ("Document", "version", True),
        ("Document", "version", 0),
        ("Document", "scope", "user:.."),
        ("Document", "scope", "user:a\n"),
        ("Document", "path", "../escape"),
        ("Document", "path", "a/./b"),
        ("Document", "path", "a\n"),
        ("Document", "created_at", "2026-08-19T10:00:00"),
        ("Mailbox", "format", 2),
        ("Mailbox", "occurrence", 0),
        ("Mailbox", "status", "missing"),
        ("Mailbox", "conversation", "../escape"),
        ("Mailbox", "turn", 1),  # fixture has no destination conversation
    ],
)
def test_invalid_records_are_not_the_supported_format(
    kind: str, key: str, bad: Any
) -> None:
    data = fixture("records.json")[kind.lower()]
    data[key] = bad
    with pytest.raises(ValidationError):
        validate(kind, data)


def test_redacted_turns_have_no_messages() -> None:
    turns = fixture("records.json")["turns"]
    for original, change in [(turns[0], []), (turns[1], turns[0]["messages"])]:
        changed = copy.deepcopy(original)
        changed["messages"] = change
        with pytest.raises(ValidationError):
            validate("Turn", changed)


def test_versioned_files_and_wire_records_agree(binary: Binary) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    data = fixture("records.json")
    with serve(binary) as server:
        assert server.post("memory/read", scope="user:contract", path="notes/fact") == {
            "document": data["document"]
        }
        assert server.post(
            "conversation/read_turns", conversation_id="contract-session"
        ) == {"turns": data["turns"], "next_after": None}
        assert server.post(
            "conversation/read_projections", conversation_id="contract-session"
        ) == {"entries": data["projections"], "next_after": None}
        assert server.post(
            "conversation/turn_redactions", conversations=["contract-session"]
        ) == {"redactions": [data["turn_redaction"]]}
        assert server.post("memory/redactions", scope="user:contract") == {
            "redactions": [data["redaction"]],
            "next_cursor": None,
        }
        mailbox = server.post(
            "memory/read",
            scope="user:contract",
            path="messages/" + data["mailbox"]["id"],
        )["document"]
        assert json.loads(mailbox["content"]) == data["mailbox"]


@pytest.mark.parametrize("row", ["turns.jsonl", "projections.jsonl"])
def test_a_newer_stored_format_is_refused(binary: Binary, row: str) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    path = binary.home / "conversations" / "contract-session" / row
    path.write_text(
        path.read_text().replace('"neosian_format":1', '"neosian_format":2')
    )
    route = "read_turns" if row == "turns.jsonl" else "read_projections"
    with serve(binary) as server:
        response = server.request(
            "/v1/conversation/" + route, {"conversation_id": "contract-session"}
        )
        assert response.status == 400
        assert response.body["error"]["code"] == "agent_conversation_format_unsupported"
        assert "newer than supported" in response.body["error"]["message"]


def test_newer_memory_format_is_refused(binary: Binary) -> None:
    shutil.copytree(FIXTURES / "home", binary.home)
    path = binary.home / "user%3Acontract" / "documents" / "notes" / "fact.md"
    with serve(binary) as server:
        path.write_text(
            path.read_text().replace("neosian_format: 1", "neosian_format: 2")
        )
        response = server.request(
            "/v1/memory/read", {"scope": "user:contract", "path": "notes/fact"}
        )
        assert response.status == 400
        assert response.body["error"]["code"] == "memory_format_unsupported"


def test_openapi_route_set_is_explicit() -> None:
    assert OPENAPI["openapi"] == "3.1.1"
    assert OPENAPI["info"]["version"] == "6"
    posts = {path for path, methods in OPENAPI["paths"].items() if "post" in methods}
    assert posts == {
        "/v1/" + name
        for name in (
            "memory/read",
            "memory/write",
            "memory/delete",
            "memory/rename",
            "memory/list_documents",
            "memory/versions",
            "memory/redact",
            "memory/history",
            "memory/redactions",
            "conversation/append_turn",
            "conversation/read_turns",
            "conversation/last_turn_number",
            "conversation/search_turns",
            "conversation/append_projections",
            "conversation/read_projections",
            "conversation/redact_turns",
            "conversation/turn_redactions",
            "store/scopes",
            "store/conversations",
            "store/restore_scope",
            "store/restore_conversation",
        )
    }
    assert set(OPENAPI["paths"]) - posts == {"/health", "/v1/capabilities"}
    assert OPENAPI["paths"]["/health"]["get"]["security"] == []
    for methods in OPENAPI["paths"].values():
        for operation in methods.values():
            bodies = [
                operation.get("requestBody", {}),
                *operation["responses"].values(),
            ]
            for body in bodies:
                for media in body.get("content", {}).values():
                    Draft202012Validator.check_schema(media["schema"])


def test_the_contract_tier_did_not_import_the_python_product() -> None:
    assert not any(
        name == "neosian" or name.startswith("neosian.") for name in sys.modules
    )
