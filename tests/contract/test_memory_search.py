"""Mounted CLI/MCP search, distinct from the startup index and history search."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.profile_support import PROFILE, WORKSPACE, bind, install, rpc, run
from tests.contract.support import ADMIN, Binary, serve


@pytest.mark.parametrize("mode", ["file", "remote", "profile"])
def test_cli_search_returns_authoritative_references(binary: Binary, mode: str) -> None:
    with serve(binary) as server:
        document = server.post(
            "memory/write",
            scope="workspace:portable",
            path="notes/rule",
            content="Use the blue deployment window.",
            actor="source",
        )["document"]
        flags = ["--mount", "scope=workspace:portable,path=project,ro"]
        if mode == "remote":
            flags += ["--url", server.url]
        elif mode == "profile":
            install(
                binary, PROFILE.replace('injection = "index"', 'injection = "none"')
            )
            bind(binary, "--root", str(binary.home))
            flags = ["--profile", "portable"]
        result = run(
            binary,
            "memory",
            "search",
            "BLUE window",
            *flags,
            "--json",
            env={"NEOSIAN_CLIENT_TOKEN": ADMIN},
        )
        assert result.returncode == 0, result.stderr
        page = json.loads(result.stdout)
        assert page["next_cursor"] is None and len(page["hits"]) == 1
        hit = page["hits"][0]
        assert hit["reference"] == "/project/notes/rule"
        assert hit["path"] == "notes/rule" and hit["scope"] == "workspace:portable"
        assert (
            hit["version"] == document["version"] and hit["actor"] == document["actor"]
        )
        assert hit["excerpt"]["text"] == document["content"]
        if mode == "profile":
            assert (hit["source"], hit["workspace_id"]) == ("knowledge", WORKSPACE)
        else:
            assert "source" not in hit and "workspace_id" not in hit


def test_mcp_search_finds_content_created_after_the_initial_index(
    binary: Binary,
) -> None:
    install(binary, PROFILE.replace('injection = "index"', 'injection = "none"'))
    bind(binary, "--root", str(binary.home))
    answers = rpc(
        binary,
        [
            (
                "memory",
                {
                    "command": "create",
                    "path": "/project/decision",
                    "content": "Canary deployments use fifteen minutes.",
                },
            ),
            ("search_memory", {"query": "canary fifteen"}),
            ("memory", {"command": "view", "path": "/project/decision"}),
            ("search_memory", {"query": "canary", "path": "/other"}),
            ("search_memory", {"query": "canary", "limit": True}),
            ("search_memory", {"query": "canary", "fields": [2]}),
            ("search_memory", {"query": "canary", "unknown": "ignored?"}),
        ],
    )
    assert "Canary" not in answers[0]["result"]["instructions"]
    assert "/project/decision" not in answers[0]["result"]["instructions"]
    tools = {t["name"]: t for t in answers[1]["result"]["tools"]}
    assert "search_memory" in tools and "search_history" not in tools
    assert tools["search_memory"]["annotations"]["readOnlyHint"] is True
    schema = tools["search_memory"]["inputSchema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["fields"]["items"] == {"type": "string"}
    page = json.loads(answers[3]["result"]["content"][0]["text"])
    assert page["hits"][0]["reference"] == "/project/decision"
    assert "fifteen minutes" in answers[4]["result"]["content"][0]["text"]
    assert all(answer["result"]["isError"] is True for answer in answers[5:])


def test_multiple_profile_sources_page_without_losing_boundaries(
    binary: Binary,
) -> None:
    profile = PROFILE.replace(
        'sources = ["knowledge"]', 'sources = ["knowledge", "other"]'
    )
    profile += '\n[[mounts]]\nsource = "other"\nscope = "workspace:other"\npath = "zeta"\naccess = "ro"\n'
    install(binary, profile)
    second = binary.directory / "second-store"
    bind(binary, "--root", str(binary.home))
    bind(binary, "--root", str(second), source="other")
    # Explicit roots in a directory with no automatic profile selection remain legacy calls.
    for scope, root, path in [
        ("workspace:portable", binary.home, "z"),
        ("workspace:portable", binary.home, "a"),
        ("workspace:other", second, "b"),
    ]:
        binary.json(
            "memory",
            "create",
            "/memories/" + path,
            "--content",
            "needle",
            "--scope",
            scope,
            "--root",
            str(root),
        )
    seen: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(4):
        flags = ["--cursor", cursor] if cursor else []
        page = binary.json(
            "memory",
            "search",
            "needle",
            "--profile",
            "portable",
            "--limit",
            "1",
            *flags,
        )
        seen += page["hits"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [h["reference"] for h in seen] == ["/project/a", "/project/z", "/zeta/b"]
    assert [h["source"] for h in seen] == ["knowledge", "knowledge", "other"]
    assert cursor is None
    first = binary.json(
        "memory", "search", "needle", "--profile", "portable", "--limit", "1"
    )
    changed = run(
        binary,
        "memory",
        "search",
        "needle",
        "--profile",
        "portable",
        "--path",
        "/zeta",
        "--cursor",
        first["next_cursor"],
        "--json",
    )
    assert changed.returncode == 1 and "different query or selection" in changed.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        [""],
        ["needle", "--field", "metadata"],
        ["needle", "--limit", "0"],
        ["needle", "--path", "relative"],
        ["needle", "--path", "/unmounted"],
        ["needle", "--metadata-key", "secret"],
    ],
)
def test_invalid_cli_search_does_not_create_a_store(
    binary: Binary, arguments: list[str]
) -> None:
    result = binary.run(
        "memory", "search", *arguments, "--scope", "user:test", "--json"
    )
    assert result.returncode == 2, result.stderr
    assert not binary.home.exists()


def test_file_search_refuses_new_formats_and_unsupported_selected_metadata(
    binary: Binary,
) -> None:
    root = binary.home / "user%3Atest" / "documents"
    root.mkdir(parents=True)
    doc = root / "doc.md"
    envelope = (
        "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-24T10:00:00Z'\n"
        "updated_at: '2026-10-24T10:00:00Z'\ntags:\n  - legacy\n---\nneedle"
    )
    doc.write_text(envelope, encoding="utf-8")
    assert binary.json("memory", "search", "needle", "--scope", "user:test")["hits"]
    refused = binary.run(
        "memory",
        "search",
        "legacy",
        "--scope",
        "user:test",
        "--field",
        "metadata",
        "--metadata-key",
        "tags",
        "--json",
    )
    assert refused.returncode == 1 and "one-line scalar" in refused.stderr
    doc.write_text(
        envelope.replace("neosian_format: 1", "neosian_format: 99"), encoding="utf-8"
    )
    before = doc.read_bytes()
    refused = binary.run("memory", "search", "needle", "--scope", "user:test", "--json")
    assert refused.returncode == 1 and "format 99" in refused.stderr
    assert doc.read_bytes() == before
