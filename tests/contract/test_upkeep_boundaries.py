"""Authority comes from the selected storage and caller, never review data."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.knowledge_support import marked, success, unpack
from tests.contract.profile_support import (
    PROFILE,
    WORKSPACE,
    bind,
    grant_env,
    install,
    rpc,
)
from tests.contract.support import Binary, fixture, serve
from tests.contract.upkeep_support import digest, edit, invoke, prepare, save


@pytest.mark.parametrize(
    "path",
    [
        "messages/a",
        "message-links/a",
        "sessions/a",
        "handoff",
        "notes/.collection",
        "MESSAGES/a",
    ],
)
def test_reserved_targets_are_refused_even_with_explicit_requests(
    binary: Binary, path: str
) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope="user:upkeep", path=path, content="reserved")
        request = save(
            binary,
            "request.json",
            {"format": 1, "changes": [edit("/memories/" + path)]},
        )
        refused = invoke(binary, "prepare", str(request))
        assert refused.returncode == 1 and "reserved" in refused.stderr
        assert (
            server.post("memory/read", scope="user:upkeep", path=path)["document"][
                "content"
            ]
            == "reserved"
        )


@pytest.mark.parametrize("access", ["ro", "eo"])
def test_read_only_and_edit_only_policy_is_rechecked_at_apply(
    binary: Binary, access: str
) -> None:
    with serve(binary) as server:
        base = fixture("records.json")["document"]
        server.post(
            "store/restore_scope",
            scope="user:upkeep",
            documents=[
                {**base, "scope": "user:upkeep", "path": "notes/a", "content": ""}
            ],
            versions=[],
            redactions=[],
        )
        deletion = {
            "path": "/memories/notes/a",
            "operation": "delete",
            "expected_version": 2,
            "reason": "Empty",
        }
        path, proposal = prepare(binary, [deletion])
        flags = ["--mount", f"scope=user:upkeep,path=memories,{access}"]
        denied = invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
        )
        assert denied.returncode == 1 and (
            "read-only" in denied.stderr or "edit-only" in denied.stderr
        )
        request = save(
            binary, "edit.json", {"format": 1, "changes": [edit(expected_version=2)]}
        )
        result = invoke(binary, "prepare", str(request), flags=flags)
        assert result.returncode == (1 if access == "ro" else 0)
        if access == "eo":
            replacement = json.loads(result.stdout)
            file = save(binary, "replace.json", replacement)
            success(
                invoke(
                    binary,
                    "apply",
                    str(file),
                    "--write",
                    "--approve",
                    replacement["id"],
                    flags=flags,
                )
            )
            assert (
                server.post("memory/read", scope="user:upkeep", path="notes/a")[
                    "document"
                ]["version"]
                == 3
            )


def test_rules_are_reviewed_sources_and_invalid_attributes_can_be_repaired(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        definition = {
            "format": 1,
            "name": "Decisions",
            "schema": {
                "fields": {"status": {"type": "string", "required": True}},
                "allow_unknown": False,
            },
        }
        rules = "<!-- neosian-collection:1\n" + json.dumps(definition) + "\n-->\n"
        server.post(
            "memory/write", scope="user:upkeep", path="notes/.collection", content=rules
        )
        server.post(
            "memory/write",
            scope="user:upkeep",
            path="notes/a",
            content=marked("Current decision"),
        )
        findings = success(invoke(binary, "scan", "/memories/notes"))["findings"]
        assert findings[0]["issues"][0]["kind"] == "invalid_structure"
        request = edit(attributes={"status": "accepted"})
        path, proposal = prepare(binary, [request])
        server.post(
            "memory/write",
            scope="user:upkeep",
            path="notes/.collection",
            content=rules.replace('"string"', '"integer"'),
        )
        assert (
            invoke(
                binary, "apply", str(path), "--write", "--approve", proposal["id"]
            ).returncode
            == 1
        )
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"][
                "version"
            ]
            == 1
        )
        server.post(
            "memory/write", scope="user:upkeep", path="notes/.collection", content=rules
        )
        path, proposal = prepare(binary, [request])
        success(
            invoke(binary, "apply", str(path), "--write", "--approve", proposal["id"])
        )
        assert success(invoke(binary, "scan", "/memories/notes"))["findings"] == []


@pytest.mark.parametrize("client", ["codex", "claude-code", "cursor"])
def test_mcp_proposals_preview_and_apply_through_portable_authority(
    binary: Binary, client: str
) -> None:
    install(binary, PROFILE.replace('injection = "index"', 'injection = "none"'))
    bind(binary, "--root", str(binary.home))
    with serve(binary) as server:
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="notes/a",
            content="Original",
        )
        request = {"format": 1, "changes": [edit("/project/notes/a")]}
        answers = rpc(
            binary,
            [("upkeep_review", {"command": "prepare", "data": json.dumps(request)})],
            actor=f"mcp:{client}",
        )
        tools = {t["name"]: t for t in answers[1]["result"]["tools"]}
        assert tools["upkeep_review"]["annotations"]["readOnlyHint"] is True
        assert tools["upkeep_apply"]["annotations"]["destructiveHint"] is True
        assert tools["upkeep_apply"]["annotations"]["idempotentHint"] is False
        proposal = json.loads(answers[2]["result"]["content"][0]["text"])
        assert proposal["workspace"] == WORKSPACE and all(
            g["source"] == "knowledge" for g in proposal["observations"]
        )
        encoded = json.dumps(proposal)
        calls: list[tuple[str, dict[str, Any]]] = [
            ("upkeep_apply", {"proposal": encoded}),
            ("upkeep_apply", {"proposal": encoded, "write": True}),
            (
                "upkeep_apply",
                {"proposal": encoded, "write": True, "approve": proposal["id"]},
            ),
            ("upkeep_review", {"command": "inspect", "data": encoded}),
        ]
        replies = rpc(binary, calls, actor=f"mcp:{client}")
        assert json.loads(replies[2]["result"]["content"][0]["text"])["preview"]
        assert replies[3]["result"]["isError"] is True
        assert (
            json.loads(replies[4]["result"]["content"][0]["text"])["applied"][0][
                "version"
            ]
            == 2
        )
        doc = server.post("memory/read", scope="workspace:portable", path="notes/a")[
            "document"
        ]
        assert (
            doc["actor"] == f"mcp:{client}"
            and unpack(doc["content"])[1] == "Corrected café.\n"
        )


def test_remote_read_permission_cannot_apply_a_writer_approved_proposal(
    binary: Binary,
) -> None:
    with serve(binary, extra_env=grant_env(binary.directory / "grants.toml")) as server:
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="notes/a",
            content="Approved actor is not authority",
        )
        install(binary)
        bind(binary, "--url", server.url, "--token-env", "KNOWLEDGE_TOKEN")
        flags = ["--profile", "portable"]
        path, proposal = prepare(
            binary,
            [edit("/project/notes/a")],
            flags,
            {"KNOWLEDGE_TOKEN": "contract-admin"},
        )
        denied = invoke(
            binary,
            "apply",
            str(path),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
            env={"KNOWLEDGE_TOKEN": "reader-token"},
        )
        assert denied.returncode == 1
        report = json.loads(denied.stdout)
        assert report["applied"] == [] and not report["success"]
        assert "allowance does not reach the requested operation" in report["error"]
        assert (
            server.post("memory/read", scope="workspace:portable", path="notes/a")[
                "document"
            ]["version"]
            == 1
        )
        proposal["observations"][0]["scope"] = "workspace:other"
        proposal["id"] = digest(proposal)
        forged = save(binary, "forged-scope.json", proposal)
        refused = invoke(
            binary,
            "apply",
            str(forged),
            "--write",
            "--approve",
            proposal["id"],
            flags=flags,
            env={"KNOWLEDGE_TOKEN": "reader-token"},
        )
        assert refused.returncode == 1 and "binding changed" in refused.stderr


@pytest.mark.parametrize(
    "extra",
    [{"approved": True}, {"format": 9}, {"changes": []}, {"policy": {"write": True}}],
)
def test_bad_request_format_and_embedded_approval_are_refused_before_opening_state(
    binary: Binary, extra: dict[str, Any]
) -> None:
    request = save(binary, "invalid.json", {"format": 1, "changes": [edit()], **extra})
    assert invoke(binary, "prepare", str(request)).returncode == 1
    assert not binary.home.exists()


@pytest.mark.parametrize("imported", [False, True])
def test_tampered_digest_and_dropped_provenance_cannot_apply(
    binary: Binary, imported: bool
) -> None:
    with serve(binary) as server:
        source = {"label": "keep this source", "file": "origin.md", "sha256": "a" * 64}
        server.post(
            "memory/write",
            scope="user:upkeep",
            path="notes/a",
            content=marked("old", sources=[source]) if imported else "old",
        )
        _, proposal = prepare(binary, [edit()])
        proposal["changes"][0]["content"] = "Forged replacement"
        file = save(binary, "tampered.json", proposal)
        assert (
            invoke(
                binary, "apply", str(file), "--write", "--approve", proposal["id"]
            ).returncode
            == 1
        )
        proposal["id"] = digest(proposal)
        file = save(binary, "resigned.json", proposal)
        assert (
            invoke(
                binary, "apply", str(file), "--write", "--approve", proposal["id"]
            ).returncode
            == 1
        )
        assert (
            server.post("memory/read", scope="user:upkeep", path="notes/a")["document"][
                "version"
            ]
            == 1
        )
