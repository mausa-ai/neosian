"""Portable skills, explicit changes, provenance and recovery through public doors."""

from __future__ import annotations

import json
import tomllib

import pytest

from tests.contract.knowledge_support import marked, success, unpack
from tests.contract.profile_support import PROFILE, bind, install, rpc, run
from tests.contract.skill_support import RESOURCE, SKILL, invoke, package, sha
from tests.contract.support import Binary, serve


@pytest.mark.parametrize("mode", ["file", "http", "profile"])
def test_package_preview_load_export_and_duplicate(binary: Binary, mode: str) -> None:
    source = package(binary.directory / "package")
    with serve(binary) as server:
        flags = ["--scope", "user:skills"]
        env = {"NEOSIAN_CLIENT_TOKEN": server.token}
        target = "/memories/skills/review"
        if mode == "http":
            flags += ["--url", server.url]
        if mode == "profile":
            install(binary, PROFILE.replace("workspace:portable", "user:skills"))
            bind(binary, "--root", str(binary.home))
            flags = ["--profile", "portable"]
            target = "/project/skills/review"
        args = ["import", str(source), "--to", target]
        preview = success(invoke(binary, *args, flags=flags, env=env))
        assert preview["preview"] and len(preview["actions"]) == 2
        assert (
            server.post("memory/read", scope="user:skills", path="skills/review")[
                "document"
            ]
            is None
        )
        applied = success(invoke(binary, *args, "--write", flags=flags, env=env))
        assert len(applied["applied"]) == 2
        root = server.post("memory/read", scope="user:skills", path="skills/review")[
            "document"
        ]
        assert unpack(root["content"])[1] == SKILL
        loaded = success(invoke(binary, "load", target, flags=flags, env=env))
        assert loaded["content"] == "Keep the reasoning."
        assert loaded["details"]["resources"][0]["version"] == 1
        assert loaded["details"]["collections"] == [
            target.split("/skills/")[0] + "/decisions"
        ]
        repeated = success(invoke(binary, *args, "--write", flags=flags, env=env))
        assert all(a["action"] == "duplicate" for a in repeated["actions"])
        assert repeated["applied"] == []
        output = binary.directory / "exported"
        success(
            invoke(
                binary, "export", target, "--output", str(output), flags=flags, env=env
            )
        )
        metadata, body = unpack((output / "SKILL.md").read_bytes().decode())
        assert body == SKILL
        assert metadata["sources"][-1]["origin"]["version"] == 1
        assert (
            unpack((output / "references/reason.md").read_bytes().decode())[1]
            == RESOURCE
        )
        copied = target.replace("review", "copied")
        success(
            invoke(
                binary,
                "import",
                str(output),
                "--to",
                copied,
                "--write",
                flags=flags,
                env=env,
            )
        )
        copy = server.post("memory/read", scope="user:skills", path="skills/copied")[
            "document"
        ]
        assert unpack(copy["content"])[1] == SKILL
        assert copy["version"] == 1
        assert (
            invoke(
                binary, "export", target, "--output", str(output), flags=flags, env=env
            ).returncode
            != 0
        )


def test_edit_diff_revert_and_erasure_fences(binary: Binary) -> None:
    source = package(binary.directory / "package")
    path = "/memories/skills/review"
    success(invoke(binary, "import", str(source), "--to", path, "--write"))
    edited = binary.directory / "edit.md"
    edited.write_text(SKILL.replace("reasoning", "evidence"), encoding="utf-8")
    args = ["edit", path, "--file", str(edited), "--expect-version", "1"]
    assert success(invoke(binary, *args))["preview"]
    assert success(invoke(binary, *args, "--write"))["applied"][0]["version"] == 2
    assert invoke(binary, *args, "--write").returncode != 0
    diff = success(invoke(binary, "diff", path, "--from", "1"))
    assert "reasoning" in diff["removed"] and "evidence" in diff["added"]
    reverted = success(
        invoke(
            binary, "revert", path, "--version", "1", "--expect-version", "2", "--write"
        )
    )
    assert reverted["applied"][0]["version"] == 3
    assert "reasoning" in success(invoke(binary, "load", path))["content"]
    with serve(binary) as server:
        server.post("memory/redact", scope="user:skills", path="skills/review")
    for command in [
        ("load", path),
        ("diff", path, "--from", "1"),
        ("revert", path, "--version", "1", "--expect-version", "3", "--write"),
    ]:
        refused = invoke(binary, *command)
        assert refused.returncode != 0 and "reasoning" not in refused.stdout
    assert (
        invoke(
            binary, "import", str(source), "--to", path, "--replace", "--write"
        ).returncode
        != 0
    )


def test_profile_pins_guard_live_changes_and_export_without_copies(
    binary: Binary,
) -> None:
    install(binary, PROFILE.replace("workspace:portable", "user:skills"))
    bind(binary, "--root", str(binary.home))
    flags = ["--profile", "portable"]
    source = package(binary.directory / "package")
    path = "/project/skills/review"
    success(invoke(binary, "import", str(source), "--to", path, "--write", flags=flags))
    before = (binary.home / "profiles/portable.toml").read_bytes()
    preview = success(invoke(binary, "pin", path, flags=flags))
    assert preview["after"]["skill_pins"][path]["version"] == 1
    assert (binary.home / "profiles/portable.toml").read_bytes() == before
    success(invoke(binary, "pin", path, "--write", flags=flags))
    exported = run(binary, "profile", "export", "portable")
    definition = tomllib.loads(exported.stdout)
    assert definition["skill_pins"][path]["source"] == "knowledge"
    assert (
        "Keep the reasoning" not in exported.stdout
        and str(binary.home) not in exported.stdout
    )
    with serve(binary) as server:
        original = server.post(
            "memory/read", scope="user:skills", path="skills/review"
        )["document"]
        assert definition["skill_pins"][path]["sha256"] == sha(original["content"])
        server.post(
            "memory/write",
            scope="user:skills",
            path="skills/review",
            content=original["content"].replace("reasoning", "new reason"),
        )
        refused = invoke(binary, "load", path, flags=flags)
        assert (
            refused.returncode == 1
            and "pin" in refused.stdout
            and "new reason" not in refused.stdout
        )
        assert success(invoke(binary, "inspect", path, flags=flags))["version"] == 2
        success(invoke(binary, "pin", path, "--write", flags=flags))
        assert (
            "new reason"
            in success(invoke(binary, "load", path, flags=flags))["content"]
        )
        server.post("memory/redact", scope="user:skills", path="skills/review")
        assert invoke(binary, "pin", path, "--write", flags=flags).returncode != 0
        assert invoke(binary, "load", path, flags=flags).returncode != 0
    success(invoke(binary, "unselect", path, "--write", flags=flags))
    assert success(invoke(binary, "discover", flags=flags))["skills"] == []
    assert (
        tomllib.loads(run(binary, "profile", "export", "portable").stdout).get(
            "skill_pins", {}
        )
        == {}
    )


def test_dependency_selection_cycles_and_resource_changes(binary: Binary) -> None:
    install(binary, PROFILE.replace("workspace:portable", "user:skills"))
    bind(binary, "--root", str(binary.home))
    flags = ["--profile", "portable"]
    path = "/project/skills/review"
    source = package(binary.directory / "package", requires=[{"skill": "evidence"}])
    assert (
        invoke(
            binary, "import", str(source), "--to", path, "--write", flags=flags
        ).returncode
        != 0
    )
    with serve(binary) as server:
        server.post(
            "memory/write", scope="user:skills", path="skills/evidence", content=SKILL
        )
        success(
            invoke(binary, "import", str(source), "--to", path, "--write", flags=flags)
        )
        refused = invoke(binary, "load", path, flags=flags)
        assert refused.returncode == 1 and "not selected" in refused.stdout
        success(
            invoke(binary, "select", "/project/skills/evidence", "--write", flags=flags)
        )
        loaded = success(invoke(binary, "load", path, flags=flags))
        assert (
            loaded["details"]["dependencies"][0]["path"] == "/project/skills/evidence"
        )
        cycle = marked(
            SKILL,
            collection="skills",
            attributes={"skill": {"format": 1, "requires": [{"skill": "review"}]}},
        )
        server.post(
            "memory/write", scope="user:skills", path="skills/evidence", content=cycle
        )
        assert "cycle" in invoke(binary, "load", path, flags=flags).stdout
        server.post(
            "memory/write", scope="user:skills", path="skills/evidence", content=SKILL
        )
        server.post(
            "memory/write",
            scope="user:skills",
            path="skills/review/references/reason.md",
            content="Changed without updating root",
        )
        failed = invoke(binary, "load", path, flags=flags)
        assert failed.returncode == 1 and "supporting file changed" in failed.stdout


@pytest.mark.parametrize("client", ["codex", "claude-code", "cursor"])
def test_representative_mcp_clients_receive_same_managed_skill(
    binary: Binary, client: str
) -> None:
    install(binary)
    bind(binary, "--root", str(binary.home))
    source = package(binary.directory / "package")
    path = "/project/skills/review"
    success(
        invoke(
            binary,
            "import",
            str(source),
            "--to",
            path,
            "--write",
            flags=["--profile", "portable"],
        )
    )
    answers = rpc(
        binary,
        [("list_skills", {}), ("load_skill", {"name": path})],
        actor=f"mcp:{client}",
    )
    assert answers[3]["result"]["isError"] is False
    assert "Keep the reasoning." in answers[3]["result"]["content"][0]["text"]
    assert '"scope":"workspace:portable"' in json.dumps(answers[3]).replace('\\"', '"')


def test_same_names_remain_qualified_and_prompts_include_source_revision(
    binary: Binary,
) -> None:
    text = PROFILE.replace(
        'skills = ["/project/skills/review"]',
        'skills = ["/project/skills/review", "/personal/skills/review", "builtin:upkeep"]',
    )
    text += '\n[[mounts]]\nsource = "knowledge"\nscope = "user:personal"\npath = "personal"\naccess = "ro"\n'
    install(binary, text)
    bind(binary, "--root", str(binary.home))
    flags = ["--profile", "portable"]
    with serve(binary) as server:
        for scope, text in [
            ("workspace:portable", "PROJECT"),
            ("user:personal", "PERSONAL"),
        ]:
            server.post(
                "memory/write",
                scope=scope,
                path="skills/review",
                content=SKILL.replace("reasoning", text),
            )
        assert invoke(binary, "load", "review", flags=flags).returncode != 0
        assert (
            "PROJECT"
            in success(invoke(binary, "load", "/project/skills/review", flags=flags))[
                "content"
            ]
        )
        assert (
            "PERSONAL"
            in success(invoke(binary, "load", "/personal/skills/review", flags=flags))[
                "content"
            ]
        )
        success(invoke(binary, "pin", "/project/skills/review", "--write", flags=flags))
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "contract"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "prompts/get",
                "params": {"name": "/project/skills/review"},
            },
        ]
        process = run(
            binary,
            "mcp",
            "--profile",
            "portable",
            stdin="".join(json.dumps(r) + "\n" for r in requests),
        )
        assert process.returncode == 0, process.stderr
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        assert {p["name"] for p in replies[1]["result"]["prompts"]} == {
            "/project/skills/review",
            "/personal/skills/review",
            "builtin:upkeep",
        }
        messages = replies[2]["result"]["messages"]
        assert "PROJECT" in messages[0]["content"]["text"]
        assert '"scope":"workspace:portable"' in messages[1]["content"]["text"]
        assert '"version":1' in messages[1]["content"]["text"]
        server.post("memory/delete", scope="workspace:portable", path="skills/review")
        refused = invoke(binary, "load", "/project/skills/review", flags=flags)
        assert refused.returncode != 0 and "PERSONAL" not in refused.stdout
