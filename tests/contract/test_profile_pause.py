"""One profile conversation can stop capture at the recorder's local boundary."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from tests.contract.profile_support import (
    PREFIX,
    PROFILE,
    bind,
    grant_env,
    install,
    memory,
    rpc,
)
from tests.contract.support import Binary, serve
from tests.contract.test_profile_capture import capture_profile, contents, event


def local(binary: Binary, text: str | None = None) -> Path:
    install(binary, text or capture_profile("local"))
    bind(binary, "--root", str(binary.directory / "knowledge"))
    history = binary.directory / "history"
    bind(binary, "--root", str(history), "--capture", "local", source="history")
    return history


def test_pause_drops_only_the_open_turn_and_awaits_a_fresh_prompt(
    binary: Binary,
) -> None:
    history = local(binary)
    skill = "---\nname: review\ndescription: Review facts\n---\nCheck the shared fact."
    memory(binary, "create", "/project/skills/review", "--content", skill)
    memory(binary, "create", "/project/known", "--content", "available")
    first = event(binary, "UserPromptSubmit", prompt="previously recorded")
    conversation = first["session_id"]
    event(binary, "Stop", last_assistant_message="first")
    event(binary, "UserPromptSubmit", prompt="pending-private")
    spool = binary.home / "profile-spool"
    assert b"pending-private" in contents(spool)
    for _ in range(2):
        assert (
            binary.json("record", "pause", conversation)["disposition"]
            == "capture_paused"
        )
    marker = spool / f"{conversation}.paused"
    assert marker.read_bytes() == b""
    if os.name == "posix":
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert not list(spool.glob("*.policy"))
    assert not list(spool.glob("*.jsonl"))
    assert "known" in event(binary, "SessionStart")["context"]
    assert event(binary, "MailboxPoll")["messages"] == []
    answers = rpc(binary, [("load_skill", {"name": "/project/skills/review"})])
    assert "Check the shared fact." in answers[-1]["result"]["content"][0]["text"]
    assert (
        event(binary, "UserPromptSubmit", prompt="paused-private")["disposition"]
        == "capture_paused"
    )
    assert (
        event(binary, "Stop", last_assistant_message="paused-private")["disposition"]
        == "capture_paused"
    )
    event(binary, "UserPromptSubmit", session_id="other-native", prompt="separate")
    assert event(binary, "Stop", session_id="other-native")["disposition"] == "recorded"
    for _ in range(2):
        assert (
            binary.json("record", "resume", conversation)["disposition"]
            == "capture_resumes_on_prompt"
        )
    assert not marker.exists()
    assert (
        event(binary, "Stop", last_assistant_message="late-private")["disposition"]
        == "capture_paused"
    )
    event(binary, "UserPromptSubmit", prompt="new public prompt")
    # Repeated resume while active does not abandon a new pending turn.
    assert (
        binary.json("record", "resume", conversation)["disposition"]
        == "capture_follows_profile"
    )
    assert (
        event(binary, "Stop", last_assistant_message="new public answer")["turn"] == 2
    )
    stored = contents(history)
    for keep in [
        b"previously recorded",
        b"separate",
        b"new public prompt",
        b"new public answer",
    ]:
        assert keep in stored
    for private in [b"pending-private", b"paused-private", b"late-private"]:
        assert private not in stored + contents(spool)


def test_same_native_session_in_two_workspaces_keeps_its_policy_guard(
    binary: Binary,
) -> None:
    original = capture_profile("local")
    history = local(binary, original)
    first = event(binary, "UserPromptSubmit", prompt="first workspace span")[
        "session_id"
    ]
    second_profile = original.replace("11111111", "bbbbbbbb")
    install(binary, second_profile)
    second = event(binary, "SessionStart")["session_id"]
    assert second != first and second[-64:] == first[-64:]
    binary.json("record", "pause", second)
    spool = binary.home / "profile-spool"
    assert (spool / f"{first}.jsonl").is_file()
    assert (spool / (first[-64:] + ".policy")).is_file()
    assert (
        event(binary, "Stop", last_assistant_message="second-private")["disposition"]
        == "capture_paused"
    )
    binary.json("record", "resume", second)
    rejected = binary.run(
        "record",
        "--profile",
        "portable",
        "--agent",
        "codex",
        "--json",
        stdin=json.dumps(
            {
                "session_id": "native-session",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "new workspace draft",
            }
        ),
    )
    assert rejected.returncode == 1
    assert (
        "pending capture uses a different profile" in rejected.stderr + rejected.stdout
    )
    assert (spool / f"{second}.resuming").is_file()
    assert not (spool / f"{second}.turn").exists()
    assert not (spool / f"{second}.jsonl").exists()
    install(binary, original)
    assert event(binary, "Stop", last_assistant_message="first answer")["turn"] == 1
    assert b"first workspace span" in contents(history)
    assert b"second-private" not in contents(history)


def test_control_applies_only_in_the_current_installation(binary: Binary) -> None:
    local(binary)
    id_here = event(binary, "SessionStart")["session_id"]
    directory = binary.directory / "other-installation"
    directory.mkdir()
    other = Binary(binary.executable, directory)
    local(other)
    binary.json("record", "pause", id_here)
    there = event(other, "UserPromptSubmit", prompt="another installation")
    assert there["session_id"] != id_here and there["disposition"] == "spooled"
    other.json("record", "resume", id_here)
    assert (
        event(binary, "UserPromptSubmit", prompt="still paused")["disposition"]
        == "capture_paused"
    )


def test_shared_pause_works_without_credentials_and_filters_the_next_landing(
    binary: Binary,
) -> None:
    with serve(binary, extra_env=grant_env(binary.directory / "grants.toml")) as server:
        install(
            binary,
            PROFILE
            + f'\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\nprefix = "{PREFIX}"\n[capture]\nmode = "shared"\n',
        )
        bind(
            binary,
            "--url",
            server.url,
            "--token-env",
            "CAPTURE_TOKEN",
            "--capture",
            "shared",
        )
        credentials = {"CAPTURE_TOKEN": "contract-admin"}
        conversation = event(
            binary, "UserPromptSubmit", env=credentials, prompt="pending-private"
        )["session_id"]
        binary.json("record", "pause", conversation)
        assert (
            event(binary, "Stop", last_assistant_message="paused-private")[
                "disposition"
            ]
            == "capture_paused"
        )
        assert server.post("store/conversations") == {"conversations": []}
        binary.json("record", "resume", conversation)
        assert (
            event(
                binary, "PostToolUse", tool_name="late", tool_response="late-private"
            )["disposition"]
            == "capture_paused"
        )
        event(binary, "UserPromptSubmit", env=credentials, prompt="new prompt")
        event(binary, "Stop", env=credentials, last_assistant_message="new answer")
        turns = server.post("conversation/read_turns", conversation_id=conversation)[
            "turns"
        ]
        assert len(turns) == 1
        assert [m["content"] for m in turns[0]["messages"]] == [
            "new prompt",
            "new answer",
        ]
        summary = server.post(
            "memory/read", scope="workspace:portable", path="sessions/" + conversation
        )
        assert "private" not in json.dumps(summary)
        assert b"contract-admin" not in contents(binary.home)


def test_cursor_completion_from_the_paused_turn_cannot_reopen_it(
    binary: Binary,
) -> None:
    history = local(binary)
    cursor = partial(event, binary, agent="cursor", conversation_id="cursor-native")
    old = partial(cursor, generation_id="private-old-generation")
    middle = partial(cursor, generation_id="private-paused-generation")
    new = partial(cursor, generation_id="new-generation")
    first = old("beforeSubmitPrompt", prompt="private-before-pause")
    assert old("stop", status="completed")["disposition"] == "spooled"
    binary.json("record", "pause", first["session_id"])
    assert (
        middle("beforeSubmitPrompt", prompt="private-paused")["disposition"]
        == "capture_paused"
    )
    binary.json("record", "resume", first["session_id"])
    assert (
        old("afterAgentResponse", text="private-delayed")["disposition"]
        == "capture_paused"
    )
    assert (
        cursor("beforeSubmitPrompt", prompt="private-untagged")["disposition"]
        == "capture_paused"
    )
    assert not history.exists()
    new("beforeSubmitPrompt", prompt="new cursor prompt")
    assert (
        binary.json("record", "resume", first["session_id"])["disposition"]
        == "capture_follows_profile"
    )
    for stale in [old, middle]:
        assert (
            stale("beforeSubmitPrompt", prompt="private-replayed")["disposition"]
            == "capture_paused"
        )
        assert (
            stale("afterAgentResponse", text="private-delayed")["disposition"]
            == "capture_paused"
        )
        assert stale("postToolUse", tool_name="old")["disposition"] == "capture_paused"
        assert stale("stop", status="completed")["disposition"] == "capture_paused"
    assert (
        cursor("afterAgentResponse", text="private-untagged")["disposition"]
        == "capture_paused"
    )
    new("afterAgentResponse", text="new cursor response")
    assert new("stop", status="completed")["disposition"] == "recorded"
    stored = contents(history)
    assert b"new cursor response" in stored
    assert b"private-" not in stored + contents(binary.home / "profile-spool")


def test_interrupted_discard_stays_paused_until_cleanup_can_finish(
    binary: Binary,
) -> None:
    history = local(binary)
    conversation = event(binary, "UserPromptSubmit", prompt="pending-private")[
        "session_id"
    ]
    guard = binary.home / "profile-spool" / (conversation[-64:] + ".policy")
    guard.unlink()
    guard.mkdir()  # Simulate a guard the recorder cannot unlink as a file.
    assert binary.run("record", "pause", conversation).returncode == 1
    assert binary.run("record", "resume", conversation).returncode == 1
    result = binary.run(
        "record",
        "--profile",
        "portable",
        "--agent",
        "codex",
        stdin=json.dumps(
            {
                "session_id": "native-session",
                "hook_event_name": "Stop",
                "last_assistant_message": "paused-private",
            }
        ),
    )
    assert result.returncode == 1
    assert not history.exists()
    guard.rmdir()
    binary.json("record", "resume", conversation)
    assert (
        event(binary, "Stop", last_assistant_message="delayed-private")["disposition"]
        == "capture_paused"
    )
    event(binary, "UserPromptSubmit", prompt="public")
    assert event(binary, "Stop")["turn"] == 1
    assert b"private" not in contents(history)


@pytest.mark.parametrize("mode", ["local", "off"])
def test_first_paused_hook_finishes_cleanup_after_marker_publication(
    binary: Binary, mode: str
) -> None:
    history = local(binary)
    cid = event(binary, "UserPromptSubmit", prompt="pending-private")["session_id"]
    spool = binary.home / "profile-spool"
    (spool / f"{cid}.paused").touch(mode=0o600)
    if mode == "off":
        install(binary, capture_profile("off"))
    assert (
        event(binary, "Stop", last_assistant_message="late-private")["disposition"]
        == "capture_paused"
    )
    assert not history.exists() and b"pending-private" not in contents(spool)
    assert not list(spool.glob("*.policy"))


def test_selection_change_does_not_land_a_paused_workspaces_callback(
    binary: Binary,
) -> None:
    original = capture_profile("local")
    history = local(binary, original)
    first = event(binary, "UserPromptSubmit", prompt="private-before")["session_id"]
    binary.json("record", "pause", first)
    install(binary, original.replace("11111111", "bbbbbbbb"))
    assert (
        event(binary, "Stop", last_assistant_message="private-delayed")["disposition"]
        == "capture_paused"
    )
    assert not history.exists()
    event(binary, "UserPromptSubmit", prompt="fresh workspace")
    assert (
        event(binary, "Stop", last_assistant_message="public")["disposition"]
        == "recorded"
    )
    install(binary, original)
    assert (
        event(binary, "UserPromptSubmit", prompt="still-private")["disposition"]
        == "capture_paused"
    )
    assert b"private-" not in contents(history)


@pytest.mark.parametrize("agent", ["pi", "opencode"])
@pytest.mark.parametrize("mailbox", [False, True])
def test_normal_adapter_frame_contains_a_bounded_session_identity(
    binary: Binary, agent: str, mailbox: bool
) -> None:
    install(
        binary,
        PROFILE + f"\n[context]\nmax_chars = 512\nmailbox = {str(mailbox).lower()}\n",
    )
    bind(binary, "--root", str(binary.directory / "knowledge"))
    memory(binary, "create", "/project/fact", "--content", "public")
    start = event(binary, "SessionStart", agent=agent)
    frame = event(binary, "MailboxContext", agent=agent)["context"]
    assert frame.splitlines()[0].startswith(
        "[neosian session identity: " + start["session_id"]
    )
    instructions = rpc(binary, actor=f"mcp:{agent}")[0]["result"]["instructions"]
    assert len(frame) + len(instructions) <= 512
    binary.json("record", "pause", start["session_id"])
    assert event(binary, "MailboxContext", agent=agent)["context"] == frame


def test_pause_rejects_native_ids_and_malformed_workspace_ids(binary: Binary) -> None:
    for invalid in [
        "native-session",
        "../" + PREFIX + "a" * 64,
        PREFIX.upper() + "a" * 64,
        PREFIX + "z" * 64,
        PREFIX + "a" * 63,
    ]:
        result = binary.run("record", "pause", invalid)
        assert result.returncode == 2
    wrong_spool = binary.directory / "unrelated-spool"
    result = binary.run(
        "record", "--spool", str(wrong_spool), "pause", PREFIX + "a" * 64
    )
    assert result.returncode == 2
    assert not wrong_spool.exists()
    assert not (binary.home / "profile-spool").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file lock interoperation")
def test_pause_waits_for_the_landing_lock(binary: Binary) -> None:
    import fcntl

    local(binary)
    conversation = event(binary, "UserPromptSubmit", prompt="dropped")["session_id"]
    spool = binary.home / "profile-spool"
    with (spool / ("." + conversation[-64:] + ".lock")).open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with subprocess.Popen(
            [binary.executable, "record", "pause", conversation],
            cwd=binary.directory,
            env=binary.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as command:
            try:
                with pytest.raises(subprocess.TimeoutExpired):
                    command.wait(timeout=0.25)
                assert not (spool / f"{conversation}.paused").exists()
                assert (spool / f"{conversation}.jsonl").is_file()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
            assert command.wait(timeout=10) == 0
    assert not (spool / f"{conversation}.jsonl").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file lock interoperation")
def test_queued_hook_observes_a_pause_published_under_the_lock(binary: Binary) -> None:
    import fcntl

    history = local(binary)
    conversation = event(binary, "UserPromptSubmit", prompt="earlier prompt")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    with (spool / ("." + conversation[-64:] + ".lock")).open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with subprocess.Popen(
            [
                binary.executable,
                "record",
                "--profile",
                "portable",
                "--agent",
                "codex",
                "--json",
            ],
            cwd=binary.directory,
            env=binary.environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as hook:
            payload = json.dumps(
                {
                    "session_id": "native-session",
                    "hook_event_name": "Stop",
                    "last_assistant_message": "late-private",
                }
            )
            try:
                with pytest.raises(subprocess.TimeoutExpired):
                    hook.communicate(payload, timeout=0.25)
                # Publish precisely the state a completed pause must expose before
                # releasing the lock. A hook that checked before locking would land.
                (spool / f"{conversation}.paused").touch(mode=0o600)
                (spool / f"{conversation}.jsonl").unlink()
                (spool / (conversation[-64:] + ".policy")).unlink()
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
            out, err = hook.communicate(timeout=10)
            assert hook.returncode == 0, err
            assert json.loads(out)["disposition"] == "capture_paused"
    assert not history.exists()
