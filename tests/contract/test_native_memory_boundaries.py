"""Native inputs and local consent cannot widen capture or replace protected state."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from tests.contract.support import Binary
from tests.contract.test_native_memory import (
    DOCUMENT,
    PATH,
    SCOPE,
    hook,
    register,
    source,
    sync,
    versions,
)


@pytest.mark.parametrize(
    "damage",
    ["symlink", "directory-link", "fifo", "oversize", "invalid-utf8", "unsafe-name"],
)
def test_bad_source_refuses_before_any_copy(binary: Binary, damage: str) -> None:
    directory = source(binary)
    register(binary, directory, "--write", "--recursive")
    candidate = directory / "z.md"
    if damage in ("symlink", "directory-link"):
        target = binary.directory / "private.md"
        target.write_text("must not be read", encoding="utf-8")
        try:
            candidate.symlink_to(
                target if damage == "symlink" else binary.directory,
                target_is_directory=damage == "directory-link",
            )
        except OSError:
            pytest.skip("filesystem does not permit symlinks")
    elif damage == "fifo":
        if os.name != "posix":
            pytest.skip("FIFO is POSIX only")
        os.mkfifo(candidate)
    elif damage == "oversize":
        candidate.write_bytes(b"x" * (1024 * 1024 + 1))
    elif damage == "invalid-utf8":
        candidate.write_bytes(b"\xff")
    else:
        (directory / "unsafe name.md").write_text("no truncation", encoding="utf-8")
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1
    assert not (binary.home / "user%3Anative").exists()
    result = hook(binary, "PostToolUse", tool_name="Write")
    assert result.returncode == 0 and "1 need inspection" in result.stdout
    assert "must not be read" not in result.stdout + result.stderr


def test_shallow_default_and_explicit_file_registration(binary: Binary) -> None:
    directory = source(binary)
    nested = directory / "rollout_summaries"
    nested.mkdir()
    (nested / "session.md").write_text("raw evidence", encoding="utf-8")
    (directory / "state.json").write_text("opaque", encoding="utf-8")
    (directory / ".hidden.md").write_text("private", encoding="utf-8")
    assert len(register(binary, directory)["files"]) == 1
    assert len(register(binary, directory, "--recursive")["files"]) == 2
    assert len(register(binary, directory / "MEMORY.md")["files"]) == 1
    assert not binary.home.exists()


@pytest.mark.parametrize("count", [257, 1025])
def test_file_and_traversal_budgets_refuse_whole_scan(
    binary: Binary, count: int
) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    for n in range(count):
        (directory / f"n{n}.{'md' if count == 257 else 'txt'}").touch()
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1 and "exceeds" in result.stderr
    assert not (binary.home / "user%3Anative").exists()


def test_total_body_budget_and_nested_depth_are_bounded(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write", "--recursive")
    for n in range(9):
        (directory / f"n{n}.md").write_bytes(b"x" * 1024 * 1024)
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1 and "8 MiB" in result.stderr
    assert not (binary.home / "user%3Anative").exists()
    for file in directory.glob("n*.md"):
        file.unlink()
    (directory / "/".join(["nested"] * 9)).mkdir(parents=True)
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1 and "8 levels" in result.stderr


def test_collection_rules_cannot_be_bypassed(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    binary.json(
        "memory",
        "create",
        "/memories/native-memory/.collection",
        "--content",
        "collection rules",
        "--scope",
        SCOPE,
    )
    result = binary.run(
        "native-memory", "sync", "notes", "--scope", SCOPE, "--write", "--json"
    )
    assert result.returncode == 1
    assert "explicit_import" in result.stdout
    assert not versions(binary)


def test_global_capture_is_explicit_and_does_not_change_routing(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write", "--all-projects")
    other = binary.directory.parent / (binary.directory.name + "-other")
    other.mkdir()
    result = hook(binary, "PostToolUse", "--project", str(other), tool_name="Bash")
    assert result.returncode == 0 and "1 files saved" in result.stdout
    assert len(versions(binary)) == 1


def test_source_root_cannot_be_retargeted_or_overlap_store(binary: Binary) -> None:
    directory = source(binary)
    result = binary.run(
        "native-memory",
        "add",
        "bad",
        "--source",
        str(binary.directory),
        "--agent",
        "claude-code",
        "--to",
        "/memories",
        "--scope",
        SCOPE,
        "--write",
    )
    assert result.returncode == 1 and "overlap" in result.stderr
    register(binary, directory, "--write")
    renamed = directory.with_name("moved")
    directory.rename(renamed)
    try:
        directory.symlink_to(renamed, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not permit symlinks")
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1 and "symlink" in result.stderr


def test_unrecognized_registry_and_receipt_are_not_replaced(binary: Binary) -> None:
    directory = source(binary)
    register(binary, directory, "--write")
    sync(binary, "--write")
    receipt = next(binary.home.rglob(".native-memory/*/*.json"))
    receipt.write_text('{"format":2}', encoding="utf-8")
    result = binary.run("native-memory", "sync", "notes", "--scope", SCOPE, "--write")
    assert result.returncode == 1 and "receipt_invalid" in result.stdout
    assert len(versions(binary)) == 1
    registry = binary.home / "native-memory.json"
    data = json.loads(registry.read_text())
    data["format"] = 2
    registry.write_text(json.dumps(data), encoding="utf-8")
    result = hook(binary, "PostToolUse", tool_name="Read")
    assert result.returncode == 0 and "unsupported" in result.stderr
    assert json.loads(registry.read_text())["format"] == 2


@pytest.mark.skipif(os.name != "posix", reason="process-kill recovery tier uses POSIX")
def test_killed_capture_recovers_memory_event_and_receipt_together(
    binary: Binary,
) -> None:
    directory = source(binary, "native recovery marker\n" * 40000)
    register(binary, directory, "--write")
    process = subprocess.Popen(
        [
            str(binary.executable),
            "native-memory",
            "sync",
            "notes",
            "--scope",
            SCOPE,
            "--write",
        ],
        env=binary.environment(),
        cwd=binary.directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pending = binary.home / ".transactions/pending.json"
    try:
        deadline = time.monotonic() + 10
        while (
            not pending.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.0001)
        assert pending.exists(), "capture finished without observing its redo journal"
        process.kill()
        process.wait(timeout=10)
        assert pending.is_file()
        assert (
            "native recovery marker"
            in binary.json("memory", "view", PATH, "--scope", SCOPE)["data"]
        )
        assert not pending.exists()
        assert sync(binary, "--write")["actions"][0]["action"] == "unchanged"
        assert len(versions(binary)) == 1
        page = binary.json("changes", "read", "--scope", SCOPE)
        assert [e["path"] for e in page["events"] if e.get("path")] == [DOCUMENT]
        assert len(list(binary.home.rglob(".native-memory/*/*.json"))) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="permission failure tier uses POSIX")
def test_failed_receipt_write_reports_uncertainty_and_recovers(binary: Binary) -> None:
    if os.geteuid() == 0:
        pytest.skip("root bypasses filesystem permission failures")
    directory = source(binary)
    register(binary, directory, "--write")
    sync(binary, "--write")
    receipt = next(binary.home.rglob(".native-memory/*/*.json"))
    (directory / "MEMORY.md").write_text("changed body", encoding="utf-8")
    (directory / "later.md").write_text("later body", encoding="utf-8")
    receipt.parent.chmod(0o500)
    try:
        result = binary.run(
            "native-memory", "sync", "notes", "--scope", SCOPE, "--write", "--json"
        )
        assert result.returncode == 1
        actions = json.loads(result.stdout)["actions"]
        assert [row["action"] for row in actions] == [
            "recovery_required",
            "not_attempted",
        ]
        assert (binary.home / ".transactions/pending.json").exists()
    finally:
        receipt.parent.chmod(0o700)
    actions = sync(binary, "--write")["actions"]
    assert [row["action"] for row in actions] == ["unchanged", "create"]
    assert len(versions(binary)) == 2
    assert not (binary.home / ".transactions/pending.json").exists()
