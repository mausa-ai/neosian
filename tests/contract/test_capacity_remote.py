"""Bulk readers validate each wire page and abandon unpublished partial exports."""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.knowledge_support import invoke
from tests.contract.support import Binary
from tests.contract.test_transport import TOKEN, responder


@pytest.mark.parametrize(
    "failure", ["revoked", "repeated", "unordered", "outside", "oversized", "response"]
)
def test_paged_export_refuses_bad_pages_and_revoked_authority(
    binary: Binary, failure: str
) -> None:
    pages: list[dict[str, Any]] = []
    seen: list[str] = []

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        stamp = "2026-10-21T04:05:06Z"
        entry = {
            "path": "notes/a",
            "version": 1,
            "created_at": stamp,
            "updated_at": stamp,
            "redacted": False,
        }
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "supports_optimistic_concurrency": True}
        if path == "/v1/memory/list_documents":
            pages.append(body)
            assert body["limit"] == 256 and body["prefix"] == "notes/"
            if len(pages) == 1:
                assert body["cursor"] is None
                return 200, {"entries": [entry], "next_cursor": "opaque-next"}
            assert body["cursor"] == "opaque-next"
            if failure == "revoked":
                return 403, {
                    "error": {"code": "forbidden", "message": "revoked", "details": {}}
                }
            entry["path"] = "notes/b"
            if failure == "unordered":
                entry["path"] = "notes/a"
            if failure == "outside":
                entry["path"] = "private/secret"
            return 200, {
                "entries": [entry] * (257 if failure == "oversized" else 1),
                "next_cursor": "opaque-next" if failure == "repeated" else None,
            }
        assert path == "/v1/memory/read"
        if body["path"] == "notes/.collection":
            return 200, {"document": None}
        seen.append(body["path"])
        return 200, {
            "document": {
                **entry,
                "scope": body["scope"],
                "path": body["path"],
                "actor": None,
                "extra": {},
                "content": "x" * (5000 if failure == "response" else 10),
            }
        }

    output = binary.directory / "export"
    with responder(reply) as (url, _):
        result = invoke(
            binary,
            "export",
            "/memories/notes",
            "--output",
            str(output),
            flags=["--scope", "user:knowledge", "--url", url],
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN, "NEOSIAN_MAX_RESPONSE_BYTES": "4096"},
        )
    assert result.returncode != 0
    assert not output.exists() and not list(binary.directory.glob(".neosian-stage-*"))
    assert seen == ["notes/a"]
    assert len(pages) == (1 if failure == "response" else 2)
    if failure == "response":
        assert "NEOSIAN_MAX_RESPONSE_BYTES" in result.stderr
