"""Review, provenance, fences and recovery through public process/storage doors."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tests.contract.knowledge_support import marked, success, unpack
from tests.contract.support import ADMIN, ROOT, Binary, fixture, serve
from tests.contract.upkeep_support import digest, edit, invoke, prepare, save


@pytest.mark.parametrize("remote", [False, True])
def test_review_approval_provenance_and_retry_fence(
    binary: Binary, remote: bool
) -> None:
    with serve(binary) as server:
        source = {"label": "original import", "file": "a.md", "sha256": "a" * 64}
        original = {
            **fixture("records.json")["document"],
            "scope": "user:upkeep",
            "path": "notes/a",
            "content": marked(
                "Old café.\r\n",
                attributes={"opaque": [None, 2, True]},
                sources=[source],
            ),
        }
        server.post(
            "store/restore_scope",
            scope="user:upkeep",
            documents=[original],
            versions=[],
            redactions=[],
        )
        server.post(
            "memory/write",
            scope="user:upkeep",
            path="notes/evidence",
            content="Evidence",
        )
        flags = ["--scope", "user:upkeep", "--actor", "cli:reviewer"]
        env = {"NEOSIAN_CLIENT_TOKEN": ADMIN}
        if remote:
            flags += ["--url", server.url]
        path, proposal = prepare(
            binary,
            [edit(expected_version=2, sources=["/memories/notes/evidence"])],
            flags,
            env,
        )
        assert proposal["id"] == digest(proposal)
        schema = json.loads(
            (ROOT / "assets/contracts/upkeep-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(proposal)
        validator.validate(json.loads((binary.directory / "request.json").read_text()))
        assert "approved" not in proposal and proposal["workspace"] is None
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"]
            == original
        )
        preview = success(invoke(binary, "apply", str(path), flags=flags, env=env))
        assert preview["preview"] and preview["applied"] == []
        assert "Old café." in preview["changes"][0]["diff"]["removed"]
        refused = invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            "0" * 64,
            flags=flags,
            env=env,
        )
        assert refused.returncode == 1
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"]
            == original
        )
        applied = success(
            invoke(
                binary,
                "apply",
                str(path),
                "--write",
                "--approve",
                proposal["id"],
                flags=flags,
                env=env,
            )
        )
        assert applied["applied"] == [
            {"path": "/memories/notes/a", "operation": "replace", "version": 3}
        ]
        current = server.post("memory/read", scope="user:upkeep", path="notes/a")[
            "document"
        ]
        assert (
            current["extra"] == original["extra"]
            and current["created_at"] == original["created_at"]
        )
        assert current["actor"].endswith("cli:reviewer")
        metadata, body = unpack(current["content"])
        assert body == "Corrected café.\n" and metadata["attributes"] == {
            "opaque": [None, 2, True]
        }
        assert source in metadata["sources"]
        assert {s["origin"]["path"] for s in metadata["sources"] if "origin" in s} == {
            "notes/a",
            "notes/evidence",
        }
        assert (
            next(
                s
                for s in metadata["sources"]
                if s.get("origin", {}).get("path") == "notes/a"
            )["origin"]["extra"]
            == original["extra"]
        )
        inspected = success(invoke(binary, "inspect", str(path), flags=flags, env=env))
        assert (
            next(
                row
                for row in inspected["observations"]
                if row["path"] == "/memories/notes/a"
            )["state"]
            == "matches_proposed_content"
        )
        assert (
            invoke(
                binary,
                "apply",
                str(path),
                "--write",
                "--approve",
                proposal["id"],
                flags=flags,
                env=env,
            ).returncode
            == 1
        )
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"][
                "version"
            ]
            == 3
        )


@pytest.mark.parametrize("target", ["notes/a", "notes/evidence"])
@pytest.mark.parametrize("change", ["write", "redact", "delete"])
def test_changes_or_erasure_after_preparation_refuse_before_any_write(
    binary: Binary, target: str, change: str
) -> None:
    with serve(binary) as server:
        for name in ["a", "b", "evidence"]:
            server.post(
                "memory/write", scope="user:upkeep", path=f"notes/{name}", content=name
            )
        path, proposal = prepare(
            binary,
            [edit(sources=["/memories/notes/evidence"]), edit("/memories/notes/b")],
        )
        args: dict[str, Any] = {"scope": "user:upkeep", "path": target}
        if change == "write":
            args["content"] = "A concurrent correction"
        server.post(f"memory/{change}", **args)
        result = invoke(
            binary, "apply", str(path), "--write", "--approve", proposal["id"]
        )
        assert result.returncode == 1 and "changed" in result.stderr
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/b")["document"][
                "version"
            ]
            == 1
        )
        if change == "redact":
            assert (
                server.post("memory/read", scope="user:upkeep", path=target)[
                    "document"
                ]["version"]
                == 1
            )
            states = success(invoke(binary, "inspect", str(path)))["observations"]
            assert any(
                row["path"].endswith(target) and row["state"] == "redacted"
                for row in states
            )


def test_retained_revision_becomes_a_new_reviewed_version(binary: Binary) -> None:
    with serve(binary) as server:
        for content in ["Original fact", "Wrong correction"]:
            server.post(
                "memory/write", scope="user:upkeep", path="notes/a", content=content
            )
        request = {
            "path": "/memories/notes/a",
            "operation": "restore",
            "expected_version": 2,
            "restore_version": 1,
            "reason": "The original source remains correct",
        }
        path, proposal = prepare(binary, [request])
        forged = {
            **proposal,
            "changes": [
                {
                    **proposal["changes"][0],
                    "content": proposal["changes"][0]["content"].replace(
                        "Original fact", "Forged recovery"
                    ),
                }
            ],
        }
        forged["id"] = digest(forged)
        altered = save(binary, "altered-restoration.json", forged)
        refused = invoke(
            binary, "apply", str(altered), "--write", "--approve", forged["id"]
        )
        assert (
            refused.returncode == 1
            and "differs from the retained revision" in refused.stderr
        )
        success(
            invoke(binary, "apply", str(path), "--write", "--approve", proposal["id"])
        )
        current = server.post("memory/read", scope="user:upkeep", path="notes/a")[
            "document"
        ]
        assert (
            current["version"] == 3 and unpack(current["content"])[1] == "Original fact"
        )
        server.post("memory/redact", scope="user:upkeep", path="notes/a")
        request["expected_version"] = 3
        file = save(binary, "erased.json", {"format": 1, "changes": [request]})
        assert invoke(binary, "prepare", str(file)).returncode == 1
        assert all(
            row["redacted"] and row["content"] == ""
            for row in server.post(
                "memory/versions", scope="user:upkeep", path="notes/a"
            )["versions"]
        )


def test_scan_and_guarded_deletion_protect_fresh_and_reserved_records(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        base = fixture("records.json")["document"]
        rows = [
            {
                **base,
                "scope": "user:upkeep",
                "path": name,
                "content": content,
                "updated_at": (
                    "2999-01-01T00:00:00Z"
                    if name == "fresh"
                    else "2020-01-01T00:00:00Z"
                ),
            }
            for name, content in [
                ("a", "same"),
                ("b", "same"),
                ("empty", " \n"),
                ("fresh", ""),
                ("sessions/state", ""),
                ("handoff", ""),
                ("messages/state", ""),
                ("message-links/state", ""),
                ("notes/.collection", ""),
                ("MESSAGES/hidden", ""),
            ]
        ]
        server.post(
            "store/restore_scope",
            scope="user:upkeep",
            documents=rows,
            versions=[],
            redactions=[],
        )
        # A malformed reserved definition is never scanned or rewritten.
        scanned = success(invoke(binary, "scan"))
        assert scanned["checked"] == 4 and not scanned["truncated"]
        found = {row["path"]: row["issues"] for row in scanned["findings"]}
        assert found["/memories/b"][0] == {
            "kind": "duplicate",
            "keeper": "/memories/a",
            "eligible_for_deletion": True,
        }
        assert found["/memories/fresh"][0]["eligible_for_deletion"] is False
        assert success(invoke(binary, "scan", "--limit", "1"))["truncated"]
        deletion = {
            "path": "/memories/b",
            "operation": "delete",
            "expected_version": 2,
            "reason": "Exact copy",
            "keeper": "/memories/a",
        }
        path, proposal = prepare(binary, [deletion])
        result = success(
            invoke(binary, "apply", str(path), "--write", "--approve", proposal["id"])
        )
        assert result["applied"][0]["operation"] == "delete"
        assert (
            server.post("memory/read", scope="user:upkeep", path="b")["document"]
            is None
        )
        assert (
            server.post("memory/versions", scope="user:upkeep", path="b")["versions"][
                0
            ]["action"]
            == "deleted"
        )
        binary.json("memory", "maintain", "--scope", "user:upkeep")
        for name in [
            "sessions/state",
            "handoff",
            "messages/state",
            "message-links/state",
            "notes/.collection",
            "MESSAGES/hidden",
            "fresh",
            "a",
        ]:
            assert (
                server.post("memory/read", scope="user:upkeep", path=name)["document"]
                is not None
            )
