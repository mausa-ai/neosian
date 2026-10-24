"""Review assertions, guarded application and erasure through public doors."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from tests.contract.governance_support import (
    KEY,
    SCOPE,
    governed,
    proposal,
    review_hash,
)
from tests.contract.knowledge_support import invoke, marked, success, unpack
from tests.contract.support import ROOT, Binary, fixture, serve
from tests.contract.upkeep_support import digest, invoke as upkeep, save


@pytest.mark.parametrize("remote", [False, True])
def test_review_uses_upkeep_preserves_evidence_and_binds_exact_content(
    binary: Binary, remote: bool
) -> None:
    with serve(binary) as server:
        original = {
            **fixture("records.json")["document"],
            "scope": SCOPE,
            "path": "notes/a",
            "content": marked(
                "Retained café.\r\n", attributes={"opaque": [None, True, 2]}
            ),
        }
        server.post(
            "store/restore_scope",
            scope=SCOPE,
            documents=[original],
            versions=[],
            redactions=[],
        )
        flags = ["--scope", SCOPE, "--actor", "cli:governance"]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
        if remote:
            flags += ["--url", server.url]
        plan = proposal(binary, version=2, flags=flags, env=env)
        assert plan["id"] == digest(plan)
        raw = plan["changes"][0]["content"]
        metadata, body = unpack(raw)
        review = metadata["attributes"][KEY]["review"]
        assert review["sha256"] == review_hash(SCOPE, "notes/a", raw)
        assert review["by"] == "human:reviewer" and review["at"].endswith("Z")
        assert body == "Retained café.\r\n"
        assert metadata["attributes"]["opaque"] == [None, True, 2]
        assert metadata["sources"][0]["origin"]["extra"] == original["extra"]
        schema = json.loads(
            (ROOT / "assets/contracts/governance-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(metadata["attributes"][KEY])
        Draft202012Validator(schema).validate(
            json.loads((binary.directory / "review.json").read_text())
        )
        assert (
            server.post("memory/read", scope=SCOPE, path="notes/a")["document"]
            == original
        )
        path = save(binary, "review-proposal.json", plan)
        assert success(upkeep(binary, "apply", str(path), flags=flags, env=env))[
            "preview"
        ]
        success(
            upkeep(
                binary,
                "apply",
                str(path),
                "--write",
                "--approve",
                plan["id"],
                flags=flags,
                env=env,
            )
        )
        doc = server.post("memory/read", scope=SCOPE, path="notes/a")["document"]
        assert doc["version"] == 3 and doc["extra"] == original["extra"]
        assert doc["created_at"] == original["created_at"]
        assert doc["actor"].endswith("cli:governance")
        result = success(
            invoke(
                binary,
                "read",
                "/memories/notes/a",
                "--policy",
                "approved",
                flags=flags,
                env=env,
            )
        )
        assert (
            result["included"]
            and result["status"] == "approved"
            and result["content"] == raw
        )
        # Raw edits still work; the old approval does not follow their meaning.
        server.post(
            "memory/write",
            scope=SCOPE,
            path="notes/a",
            content=raw + "Unreviewed change",
        )
        result = success(
            invoke(
                binary,
                "read",
                "/memories/notes/a",
                "--policy",
                "approved",
                flags=flags,
                env=env,
            )
        )
        assert (
            result["status"] == "stale"
            and not result["included"]
            and "content" not in result
        )
        assert (
            upkeep(
                binary,
                "apply",
                str(path),
                "--write",
                "--approve",
                plan["id"],
                flags=flags,
                env=env,
            ).returncode
            == 1
        )
        server.post("memory/redact", scope=SCOPE, path="notes/a")
        assert (
            invoke(binary, "read", "/memories/notes/a", flags=flags, env=env).returncode
            == 1
        )
        assert proposal_fails(binary, flags, env)
        doc = server.post("memory/read", scope=SCOPE, path="notes/a")["document"]
        assert doc["redacted"] and doc["content"] == "" and doc["version"] == 4
        rows = server.post("memory/versions", scope=SCOPE, path="notes/a")["versions"]
        assert rows and all(r["redacted"] and r["content"] == "" for r in rows)
        for path in binary.home.rglob("*"):
            if path.is_file():
                assert b"PRIVATE_REVIEW_LABEL" not in path.read_bytes()


def proposal_fails(binary: Binary, flags: list[str], env: dict[str, str]) -> bool:
    return (
        invoke(
            binary,
            "govern",
            "/memories/notes/a",
            "--file",
            str(binary.directory / "review.json"),
            "--expect-version",
            "4",
            "--reason",
            "Attempt after erasure",
            flags=flags,
            env=env,
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    "field", ["body", "owners", "sources", "references", "collection", "path", "scope"]
)
def test_changed_review_subject_is_stale(binary: Binary, field: str) -> None:
    with serve(binary) as server:
        raw = governed(SCOPE, "notes/a", "needle")
        metadata, body = unpack(raw)
        scope, path = SCOPE, "notes/a"
        if field == "body":
            body += " changed"
        elif field == "owners":
            metadata["attributes"][KEY]["owners"] = ["Different owner"]
        elif field == "sources":
            metadata["sources"] = [
                {"label": "new evidence", "file": "source", "sha256": "a" * 64}
            ]
        elif field == "references":
            metadata["references"] = [{"relation": "uses", "path": "b"}]
        elif field == "collection":
            metadata["collection"] = "other"
        elif field == "path":
            path = "notes/b"
        else:
            scope = "org:other"
        server.post(
            "memory/write", scope=scope, path=path, content=marked(body, **metadata)
        )
        result = success(
            invoke(binary, "read", "/memories/" + path, flags=["--scope", scope])
        )
        assert result["status"] == "stale" and "content" not in result


@pytest.mark.parametrize(
    "fields",
    [
        {"format": 2},
        {"owners": []},
        {"owners": ["a", "a"]},
        {"owners": ["\n"]},
        {"reviewer": None},
        {"reviewer": " "},
        {"state": "draft"},
        {"effective_at": "2026-01-01T00:00:00"},
        {"expires_at": "2026-02-30T00:00:00Z"},
        {"effective_at": "2026-01-01T00:00:00+00:00"},
        {"effective_at": "2026-02-01T00:00:00Z", "expires_at": "2026-01-01T00:00:00Z"},
        {"superseded_by": {"scope": SCOPE, "path": "notes/b"}},
        {"unknown": True},
    ],
)
def test_invalid_governance_change_refuses_before_opening_storage(
    binary: Binary, fields: dict[str, Any]
) -> None:
    change = {
        "format": 1,
        "kind": "procedure",
        "owners": ["owner"],
        "state": "approved",
        "reviewer": "reviewer",
        **fields,
    }
    file = save(binary, "invalid-review.json", change)
    result = invoke(
        binary,
        "govern",
        "/memories/notes/a",
        "--file",
        str(file),
        "--expect-version",
        "1",
        "--reason",
        "Review",
    )
    assert result.returncode != 0
    assert not binary.home.exists()


def test_supersession_links_require_current_target_authority_and_state(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        source = governed(
            SCOPE,
            "notes/a",
            state="superseded",
            superseded_by={"scope": "org:other", "path": "notes/b"},
        )
        server.post("memory/write", scope=SCOPE, path="notes/a", content=source)
        server.post(
            "memory/write",
            scope="org:other",
            path="notes/b",
            content=governed("org:other", "notes/b"),
        )
        result = success(
            invoke(binary, "references", "/memories/notes/a", flags=["--scope", SCOPE])
        )
        assert result["references"][0]["status"] == "unavailable"
        flags = [
            "--mount",
            f"scope={SCOPE},path=team",
            "--mount",
            "scope=org:other,path=other,ro",
        ]
        result = success(invoke(binary, "references", "/team/notes/a", flags=flags))
        assert result["references"][0]["reference"] == "/other/notes/b"
        assert result["references"][0]["relation"] == "superseded_by"
        assert (
            success(
                invoke(
                    binary,
                    "references",
                    "/team/notes/a",
                    "--policy",
                    "approved",
                    flags=flags,
                )
            )["references"]
            == []
        )
        server.post("memory/redact", scope="org:other", path="notes/b")
        result = success(invoke(binary, "references", "/team/notes/a", flags=flags))[
            "references"
        ][0]
        assert result["status"] == "unavailable" and result["version"] is None
