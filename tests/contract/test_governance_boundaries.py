"""Governance integrates with recovery, explicit references and mounted review tools."""

from __future__ import annotations

import json

import pytest

from tests.contract.governance_support import (
    KEY,
    SCOPE,
    governed,
    proposal,
    review_hash,
)
from tests.contract.knowledge_support import invoke, marked, success, unpack
from tests.contract.profile_support import PROFILE, bind, install, rpc
from tests.contract.support import Binary, serve
from tests.contract.upkeep_support import edit, invoke as upkeep, prepare, save


def test_restoration_requires_fresh_review_and_deletion_retains_until_erasure(
    binary: Binary,
) -> None:
    flags = ["--scope", SCOPE]
    with serve(binary) as server:
        raw = governed(SCOPE, "notes/a", "RETAINED_REVIEW_BODY")
        server.post("memory/write", scope=SCOPE, path="notes/a", content=raw)
        server.post(
            "memory/write", scope=SCOPE, path="notes/a", content=raw + "Changed"
        )
        file, plan = prepare(
            binary,
            [
                edit(
                    operation="restore",
                    body=None,
                    expected_version=2,
                    restore_version=1,
                )
            ],
            flags=flags,
        )
        success(
            upkeep(
                binary,
                "apply",
                str(file),
                "--write",
                "--approve",
                plan["id"],
                flags=flags,
            )
        )
        current = success(invoke(binary, "read", "/memories/notes/a", flags=flags))
        assert current["status"] == "stale" and "content" not in current
        plan = proposal(binary, version=3)
        file = save(binary, "reapproved.json", plan)
        success(
            upkeep(
                binary,
                "apply",
                str(file),
                "--write",
                "--approve",
                plan["id"],
                flags=flags,
            )
        )
        assert (
            success(invoke(binary, "read", "/memories/notes/a", flags=flags))["status"]
            == "approved"
        )
        server.post("memory/delete", scope=SCOPE, path="notes/a")
        rows = server.post("memory/versions", scope=SCOPE, path="notes/a")["versions"]
        assert any("RETAINED_REVIEW_BODY" in row["content"] for row in rows)
        assert (
            server.post(
                "memory/search",
                scope=SCOPE,
                query="RETAINED_REVIEW_BODY",
                knowledge_policy="approved",
            )["hits"]
            == []
        )
        server.post("memory/redact", scope=SCOPE, path="notes/a")
        rows = server.post("memory/versions", scope=SCOPE, path="notes/a")["versions"]
        assert rows and all(row["content"] == "" and row["redacted"] for row in rows)


@pytest.mark.parametrize("access", ["ro", "eo"])
def test_review_changes_follow_existing_mount_write_permissions(
    binary: Binary, access: str
) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope=SCOPE, path="notes/a", content="Procedure")
        file = save(
            binary,
            "change.json",
            {"format": 1, "kind": "procedure", "owners": ["owner"], "state": "draft"},
        )
        flags = ["--mount", f"scope={SCOPE},path=team,{access}"]
        result = invoke(
            binary,
            "govern",
            "/team/notes/a",
            "--file",
            str(file),
            "--expect-version",
            "1",
            "--reason",
            "Review draft",
            flags=flags,
        )
        if access == "ro":
            assert result.returncode != 0
        else:
            plan = success(result)
            file = save(binary, "plan.json", plan)
            success(
                upkeep(
                    binary,
                    "apply",
                    str(file),
                    "--write",
                    "--approve",
                    plan["id"],
                    flags=flags,
                )
            )
            read_result = success(invoke(binary, "read", "/team/notes/a", flags=flags))
            assert read_result["status"] == "draft" and "content" not in read_result


@pytest.mark.parametrize("agent", ["codex", "claude-code", "cursor"])
def test_agent_review_tool_prepares_the_same_guarded_format(
    binary: Binary, agent: str
) -> None:
    scope = "workspace:portable"
    install(
        binary, PROFILE.replace('skills = ["/project/skills/review"]', "skills = []")
    )
    bind(binary, "--root", str(binary.home))
    binary.json(
        "memory",
        "create",
        "/project/notes/a",
        "--profile",
        "portable",
        "--content",
        "Shared procedure",
    )
    change = {
        "format": 1,
        "kind": "procedure",
        "owners": ["team"],
        "state": "approved",
        "reviewer": "human:reviewer",
    }
    answers = rpc(
        binary,
        [
            (
                "knowledge",
                {
                    "command": "prepare",
                    "path": "/project/notes/a",
                    "data": json.dumps(change),
                    "expected_version": 1,
                    "reason": "Reviewed procedure",
                },
            )
        ],
        actor=f"mcp:{agent}",
    )
    tools = {t["name"]: t for t in answers[1]["result"]["tools"]}
    assert tools["knowledge"]["annotations"]["readOnlyHint"]
    assert not tools["knowledge"]["annotations"]["destructiveHint"]
    result = answers[2]["result"]
    assert not result.get("isError")
    plan = json.loads(result["content"][0]["text"])
    raw = plan["changes"][0]["content"]
    metadata, body = unpack(raw)
    assert body == "Shared procedure"
    assert metadata["attributes"][KEY]["review"]["sha256"] == review_hash(
        scope, "notes/a", raw
    )
    assert plan["observations"][0]["scope"] == scope
    assert (
        "Shared procedure"
        in binary.json("memory", "view", "/project/notes/a", "--profile", "portable")[
            "data"
        ]
    )


def test_reference_target_policy_cannot_be_weakened_by_source_selection(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        source = marked(
            "Source",
            references=[
                {"relation": "uses", "scope": "workspace:other", "path": "notes/b"}
            ],
        )
        server.post(
            "memory/write", scope="workspace:portable", path="notes/a", content=source
        )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="notes/b",
            content=governed("workspace:other", "notes/b", state="draft"),
        )
        install(
            binary,
            PROFILE.replace('skills = ["/project/skills/review"]', "skills = []") + """
[[mounts]]
source = "knowledge"
scope = "workspace:other"
path = "other"
access = "ro"
injection = "none"
knowledge_policy = "approved"
""",
        )
        bind(binary, "--root", str(binary.home))
        result = success(
            invoke(
                binary,
                "references",
                "/project/notes/a",
                "--policy",
                "all",
                flags=["--profile", "portable"],
            )
        )["references"][0]
        assert (
            result["status"] == "unavailable"
            and result["version"] is None
            and result["reference"] is None
        )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="notes/b",
            content=governed("workspace:other", "notes/b"),
        )
        result = success(
            invoke(
                binary,
                "references",
                "/project/notes/a",
                "--policy",
                "all",
                flags=["--profile", "portable"],
            )
        )["references"][0]
        assert (
            result["status"] == "available" and result["reference"] == "/other/notes/b"
        )
