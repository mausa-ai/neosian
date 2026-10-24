"""The retained memory command ladder, with literal envelopes and exit codes."""

from __future__ import annotations

import json

import pytest

from tests.contract.support import Binary


def test_memory_commands_and_text_streams(binary: Binary) -> None:
    def memory(*args: str) -> str:
        reply = binary.json("memory", *args, "--scope", "user:contract")
        assert reply["success"] is True
        return str(reply["data"])

    assert memory("create", "/memories/note", "--content", "first\nlast") == (
        "Created /memories/note (v1)"
    )
    assert memory("view", "/memories/note") == (
        "/memories/note (v1):\n1: first\n2: last"
    )
    assert (
        memory(
            "str_replace", "/memories/note", "--old-str", "first", "--new-str", "café"
        )
        == "Edited /memories/note (v2)"
    )
    assert (
        memory(
            "insert", "/memories/note", "--insert-line", "1", "--insert-text", "middle"
        )
        == "Edited /memories/note (v3)"
    )
    assert memory("view", "/memories/note", "--view-range", "2", "-1") == (
        "/memories/note (v3):\n2: middle\n3: last"
    )
    plain = binary.run("memory", "view", "/memories/note", "--scope", "user:contract")
    assert (plain.returncode, plain.stdout, plain.stderr) == (
        0,
        "/memories/note (v3):\n1: café\n2: middle\n3: last\n",
        "",
    )
    assert memory("rename", "/memories/note", "/memories/moved") == (
        "Renamed /memories/note to /memories/moved (v1)"
    )
    assert memory("delete", "/memories/moved") == "Deleted /memories/moved"
    missing = binary.run(
        "memory", "view", "/memories/moved", "--scope", "user:contract", "--json"
    )
    assert missing.returncode == 1
    assert missing.stderr == ""
    assert json.loads(missing.stdout) == {
        "success": False,
        "error": "No document or directory at /memories/moved",
        "system_reminder": "Run the memory tool with command 'view' and path '/' first.",
    }


@pytest.mark.parametrize("mode", ["ro", "eo"])
def test_mount_policy_refuses_growth(binary: Binary, mode: str) -> None:
    refused = binary.run(
        "memory",
        "create",
        "/restricted/new",
        "--content",
        "not allowed",
        "--mount",
        f"scope=user:contract,path=restricted,{mode}",
        "--json",
    )
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["success"] is False
    assert not (binary.home / "user%3Acontract" / "documents" / "new.md").exists()


def test_bad_invocation_creates_no_home(binary: Binary) -> None:
    refused = binary.run(
        "memory", "view", "--scope", "user:a", "--mount", "scope=user:b,path=other"
    )
    assert refused.returncode == 2
    assert "--scope is the single-mount sugar" in refused.stderr
    assert not binary.home.exists()
