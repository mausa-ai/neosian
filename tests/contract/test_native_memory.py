"""Native memory capture through real CLI/hooks, without model calls or host internals."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.contract.profile_support import bind, install
from tests.contract.support import Binary, serve

SCOPE = "user:native"
PATH = "/memories/native-memory/notes/MEMORY.md"
DOCUMENT = "native-memory/notes/MEMORY.md"


def source(binary: Binary, text: str = "remember this\n") -> Path:
    directory = binary.directory / "host-memory"
    directory.mkdir(exist_ok=True)
    (directory / "MEMORY.md").write_text(text, encoding="utf-8")
    return directory


def register(
    binary: Binary, directory: Path, *extra: str, agent: str = "claude-code"
) -> Any:
    return binary.json(
        "native-memory",
        "add",
        "notes",
        "--source",
        str(directory),
        "--agent",
        agent,
        "--to",
        "/memories",
        "--scope",
        SCOPE,
        *extra,
    )


def hook(
    binary: Binary, event: str, *flags: str, agent: str = "claude-code", **fields: Any
) -> subprocess.CompletedProcess[str]:
    return binary.run(
        "record",
        "--scope",
        SCOPE,
        "--agent",
        agent,
        *flags,
        stdin=json.dumps(
            {"session_id": "native-test", "hook_event_name": event, **fields}
        ),
    )


def sync(binary: Binary, *flags: str) -> Any:
    return binary.json("native-memory", "sync", "notes", "--scope", SCOPE, *flags)


def versions(binary: Binary) -> list[Any]:
    rows: list[Any] = binary.json("memory", "versions", PATH, "--scope", SCOPE)[
        "versions"
    ]
    return rows


def test_registration_requires_write_and_scans_only_registered_sources(
    binary: Binary,
) -> None:
    directory = source(binary)
    preview = register(binary, directory)
    assert preview["preview"] and not preview["registered"]
    assert preview["files"] == [{"file": "MEMORY.md", "bytes": 14, "path": PATH}]
    assert not (binary.home / "native-memory.json").exists()
    assert (
        hook(
            binary,
            "PostToolUse",
            tool_name="Write",
            tool_input={"file_path": str(directory / "MEMORY.md")},
        ).returncode
        == 0
    )
    assert (
        "remember this"
        not in binary.json("memory", "view", "/memories", "--scope", SCOPE)["data"]
    )
    register(binary, directory, "--write")
    assert binary.json("native-memory", "list")["sources"][0]["scope"] == SCOPE
    assert sync(binary)["actions"][0]["action"] == "create"
    assert (
        "remember this"
        not in binary.json("memory", "view", "/memories", "--scope", SCOPE)["data"]
    )


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_shell_writes_and_later_background_changes_are_captured(
    binary: Binary, agent: str
) -> None:
    directory = source(binary, "---\nname: exact\n---\nα secret-free body\n")
    register(binary, directory, "--write", agent=agent)
    start = hook(binary, "SessionStart", agent=agent)
    assert start.returncode == 0, start.stderr
    assert PATH in start.stdout
    with serve(binary) as server:
        document = server.post("memory/read", scope=SCOPE, path=DOCUMENT)["document"]
        assert document["content"].endswith(
            "---\nname: exact\n---\nα secret-free body\n"
        )
        assert document["actor"] == f"{agent}:native-test"
    (directory / "MEMORY.md").write_text("changed by a shell\n", encoding="utf-8")
    reply = hook(
        binary,
        "PostToolUse",
        agent=agent,
        tool_name="Bash",
        tool_input={"command": "unparsed shell command"},
    )
    assert reply.returncode == 0, reply.stderr
    context = json.loads(reply.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "1 files saved; 0 need inspection" in context["additionalContext"]
    assert "Use neosian memory" in context["additionalContext"]
    assert "changed by a shell" not in reply.stdout
    again = hook(binary, "PostToolUse", agent=agent, tool_name="Read")
    assert again.returncode == 0 and not again.stdout
    (directory / "MEMORY.md").write_text("background update\n", encoding="utf-8")
    prompt = hook(binary, "UserPromptSubmit", agent=agent, prompt="next turn")
    assert "1 files saved" in prompt.stdout
    assert len(versions(binary)) == 3
    assert (directory / "MEMORY.md").read_text() == "background update\n"


def test_stop_catches_late_writes_without_a_continuation_loop(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write", agent="codex")
    result = hook(binary, "Stop", agent="codex", last_assistant_message="done")
    assert result.returncode == 0 and json.loads(result.stdout) == {}
    assert (
        "remember this" in binary.json("memory", "view", PATH, "--scope", SCOPE)["data"]
    )
    prompt = hook(binary, "UserPromptSubmit", agent="codex", prompt="next")
    assert "Use neosian memory" in prompt.stdout


@pytest.mark.parametrize(
    "mutation", ["edit", "delete", "erase", "erase-delete", "receipt"]
)
def test_independent_changes_and_erasure_are_never_overwritten(
    binary: Binary, mutation: str
) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    sync(binary, "--write")
    with serve(binary) as server:
        if mutation == "edit":
            server.post(
                "memory/write",
                scope=SCOPE,
                path=DOCUMENT,
                content="human edit",
                actor="cli:human",
            )
        if mutation.startswith("erase"):
            server.post("memory/redact", scope=SCOPE, path=DOCUMENT)
        if mutation in ("delete", "erase-delete"):
            server.post("memory/delete", scope=SCOPE, path=DOCUMENT, actor="cli:human")
        if mutation == "receipt":
            next(binary.home.rglob(".native-memory/*/*.json")).unlink()
        before = server.post("memory/read", scope=SCOPE, path=DOCUMENT)
        rows = server.post("memory/versions", scope=SCOPE, path=DOCUMENT)
        (directory / "MEMORY.md").write_text("new source\n", encoding="utf-8")
        refused = binary.run(
            "native-memory", "sync", "notes", "--scope", SCOPE, "--write", "--json"
        )
        assert refused.returncode == 1
        assert json.loads(refused.stdout)["actions"][0]["action"] == "refused"
        assert server.post("memory/read", scope=SCOPE, path=DOCUMENT) == before
        assert server.post("memory/versions", scope=SCOPE, path=DOCUMENT) == rows
    result = hook(binary, "PostToolUse", tool_name="Bash")
    assert result.returncode == 0 and "1 need inspection" in result.stdout
    assert "native memory" in result.stderr


def test_deleted_source_is_retained_and_remove_stops_capture(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    sync(binary, "--write")
    (directory / "MEMORY.md").unlink()
    assert sync(binary, "--write")["actions"] == []
    assert (
        "remember this" in binary.json("memory", "view", PATH, "--scope", SCOPE)["data"]
    )
    source(binary, "must not capture")
    assert binary.json("native-memory", "remove", "notes")["preview"]
    assert binary.json("native-memory", "list")["sources"]
    binary.json("native-memory", "remove", "notes", "--write")
    hook(binary, "PostToolUse", tool_name="Write")
    assert len(versions(binary)) == 1


@pytest.mark.parametrize("access", ["ro", "eo"])
def test_current_mount_authority_checked_before_source_read(
    binary: Binary, access: str
) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    (directory / "MEMORY.md").write_bytes(b"\xff")
    result = binary.run(
        "native-memory",
        "sync",
        "notes",
        "--write",
        "--mount",
        f"scope={SCOPE},path=memories,{access}",
        "--json",
    )
    assert result.returncode == 1
    assert "UTF-8" not in result.stderr
    assert "only" in result.stderr
    assert not (binary.home / "user%3Anative").exists()


def test_store_scope_project_and_agent_are_bound(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    other = binary.directory.parent / (binary.directory.name + "-other")
    other.mkdir()
    for extra, agent in [
        (("--root", str(other)), "claude-code"),
        (("--project", str(other)), "claude-code"),
        ((), "codex"),
    ]:
        result = hook(binary, "PostToolUse", *extra, agent=agent, tool_name="Read")
        assert result.returncode == 0, result.stderr
        assert not result.stdout
    mismatch = binary.run(
        "native-memory", "sync", "notes", "--scope", "user:other", "--write"
    )
    assert mismatch.returncode == 1 and "mounted scope" in mismatch.stderr
    assert not (binary.home / "user%3Anative").exists()


def test_profile_capture_off_cannot_be_bypassed_by_registration(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    install(binary)
    bind(binary, "--root", str(binary.directory / "data"))
    result = binary.run(
        "record",
        "--profile",
        "portable",
        "--json",
        stdin=json.dumps(
            {
                "session_id": "native-test",
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
            }
        ),
    )
    assert (
        result.returncode == 0
        and json.loads(result.stdout)["disposition"] == "capture_off"
    )
    assert not (binary.home / "user%3Anative").exists()
    result = binary.run(
        "native-memory", "sync", "notes", "--profile", "portable", "--write"
    )
    assert result.returncode == 1 and "without a profile" in result.stderr


def test_concurrent_hooks_create_one_revision(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: hook(binary, "PostToolUse", tool_name="Read"), range(16))
        )
    assert all(r.returncode == 0 for r in results)
    assert len(versions(binary)) == 1
    assert sum("1 files saved" in r.stdout for r in results) == 1


def test_partial_receipts_are_visible_and_retry_does_not_duplicate(
    binary: Binary,
) -> None:
    directory = source(binary)
    (directory / "a.md").write_text("new file", encoding="utf-8")
    register(binary, directory, "--write")
    binary.json("memory", "create", PATH, "--content", "occupied", "--scope", SCOPE)
    for _ in range(2):
        result = binary.run(
            "native-memory", "sync", "notes", "--scope", SCOPE, "--write", "--json"
        )
        assert result.returncode == 1
        report = json.loads(result.stdout)
        assert not report["success"] and len(report["actions"]) == 2
    rows = binary.json(
        "memory", "versions", "/memories/native-memory/notes/a.md", "--scope", SCOPE
    )["versions"]
    assert len(rows) == 1
