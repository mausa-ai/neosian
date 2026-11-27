"""Capture consent and scrubbing precede spool, backend writes and summaries.
A Stop whose turn goes to a server, or to a profile with any source that is
not local, answers `pending` and leaves the turn to the lander it starts
(DESIGN §40.54 item 17.1, ruling D6 of the T1e1 plan); those contracts wait
for the turn with a bounded poll."""

from __future__ import annotations

import hashlib
import json
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import slots_held
from tests.contract.profile_support import (
    PREFIX,
    PROFILE,
    bind,
    grant_env,
    install,
    rpc,
    run,
)
from tests.contract.support import Binary, Server, serve


def capture_profile(mode: str, policy: str = "") -> str:
    text = PROFILE.replace(
        'sources = ["knowledge"]', 'sources = ["knowledge", "history"]'
    )
    return (
        text
        + f"""
[history]
source = "history"
scope = "workspace:portable"
prefix = "{PREFIX}"

[capture]
mode = "{mode}"
{policy}
""".replace(
            'scope = "workspace:portable"\nprefix',
            'scope = "workspace:history"\nprefix',
        )
    )


def event(
    binary: Binary,
    name: str,
    *,
    agent: str = "codex",
    env: dict[str, str] | None = None,
    **payload: Any,
) -> Any:
    process = run(
        binary,
        "record",
        "--profile",
        "portable",
        "--agent",
        agent,
        "--json",
        env=env,
        stdin=json.dumps(
            {"session_id": "native-session", "hook_event_name": name, **payload}
        ),
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def until(check: Callable[[], Any], what: str, seconds: float = 30.0) -> Any:
    """Polls `check` until it answers something true, which is returned."""
    deadline = time.monotonic() + seconds
    while not (found := check()):
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)
    return found


def landed(server: Server, conversation: str) -> list[Any]:
    """The conversation's turns once the lander has stored one."""
    read = lambda: server.post(  # noqa: E731
        "conversation/read_turns", conversation_id=conversation
    )["turns"]
    return list(until(read, f"a turn of {conversation}"))


def contents(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_new_profiles_capture_nothing(binary: Binary) -> None:
    install(binary)
    data = binary.directory / "data"
    bind(binary, "--root", str(data))
    events: list[tuple[str, dict[str, Any]]] = [
        ("UserPromptSubmit", {"prompt": "PRIVATE"}),
        ("PostToolUse", {"tool_name": "shell", "tool_response": "PRIVATE"}),
        ("Stop", {"last_assistant_message": "PRIVATE"}),
    ]
    for name, payload in events:
        assert event(binary, name, **payload)["disposition"] == "capture_off"
    assert not data.exists()
    assert not (binary.home / "profile-spool").exists()


def test_local_capture_filters_before_any_persistence(binary: Binary) -> None:
    policy = """fields = ["prompt", "assistant", "tool_inputs", "tool_outputs"]
exclude_tools = ["secret-tool"]
remove_keys = ["password"]
replacements = [{from = "SCRUBME", to = "[removed]"}]
"""
    install(binary, capture_profile("local", policy))
    data, history = binary.directory / "data", binary.directory / "private-history"
    bind(binary, "--root", str(data))
    bind(binary, "--root", str(history), "--capture", "local", source="history")
    event(binary, "UserPromptSubmit", prompt="keep SCRUBME")
    event(
        binary,
        "PostToolUse",
        tool_name="read",
        tool_input={"nested": {"password": "PRIVATE", "safe": "SCRUBME"}},
        tool_response={"text": "SCRUBME"},
    )
    excluded = event(
        binary, "PostToolUse", tool_name="secret-tool", tool_input={"x": "EXCLUDED"}
    )
    assert excluded["disposition"] == "excluded"
    pending = contents(binary.home / "profile-spool")
    assert (
        b"SCRUBME" not in pending
        and b"PRIVATE" not in pending
        and b"EXCLUDED" not in pending
    )
    done = event(binary, "Stop", last_assistant_message="answer SCRUBME")
    assert done["disposition"] == "recorded"
    assert done["conversation_id"].startswith(PREFIX)
    saved = contents(history)
    assert b"[removed]" in saved
    assert b"native-session" in saved  # Original client identity remains provenance.
    assert (
        b"SCRUBME" not in saved and b"PRIVATE" not in saved and b"EXCLUDED" not in saved
    )
    assert b"answer" not in contents(data)
    assert not list((binary.home / "profile-spool").glob("*.policy"))


def test_local_capture_cannot_overlap_a_memory_root(binary: Binary) -> None:
    install(binary, capture_profile("local"))
    data = binary.directory / "data"
    bind(binary, "--root", str(data))
    bind(
        binary, "--root", str(data / "history"), "--capture", "local", source="history"
    )
    result = binary.run(
        "record",
        "--profile",
        "portable",
        "--json",
        stdin=json.dumps(
            {
                "session_id": "s",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "PRIVATE",
            }
        ),
    )
    assert result.returncode != 0
    assert not data.exists()


def test_policy_change_holds_the_span_and_capture_off_retains_it(
    binary: Binary,
) -> None:
    text = capture_profile("local")
    install(binary, text)
    data, history = binary.directory / "data", binary.directory / "history"
    bind(binary, "--root", str(data))
    bind(binary, "--root", str(history), "--capture", "local", source="history")
    event(binary, "UserPromptSubmit", prompt="held")
    spool = binary.home / "profile-spool"
    before = contents(spool)
    install(binary, text.replace('mode = "local"', 'mode = "off"'))
    assert (
        event(binary, "Stop", last_assistant_message="PRIVATE")["disposition"]
        == "capture_off_pending"
    )
    assert contents(spool) == before
    # #447, ruling D5: the span recorded under the earlier policy is held with
    # policy_changed; the reply recorded under the new one lands as its own turn.
    install(binary, text + 'fields = ["assistant"]\n')
    done = event(binary, "Stop", last_assistant_message="PRIVATE")
    assert (done["disposition"], done["turn"]) == ("recorded", 1)
    held = spool / "held" / done["conversation_id"]
    assert b'"held"' in (held / "1.jsonl").read_bytes()
    assert json.loads((held / "1.reason").read_text()) == {"reason": "policy_changed"}
    assert b"PRIVATE" in contents(history) and b'"held"' not in contents(history)
    install(binary, text)
    assert (
        event(binary, "Stop", last_assistant_message="safe")["disposition"]
        == "recorded"
    )
    assert b"safe" in contents(history) and b'"held"' not in contents(history)
    assert (held / "1.jsonl").is_file()  # A held turn is never retried by itself.


@pytest.mark.parametrize(
    "order", [("stop", "afterAgentResponse"), ("afterAgentResponse", "stop")]
)
def test_cursor_completion_without_assistant_capture(
    binary: Binary, order: tuple[str, str]
) -> None:
    install(binary, capture_profile("local", 'fields = ["prompt"]'))
    bind(binary, "--root", str(binary.directory / "data"))
    history = binary.directory / "history"
    bind(binary, "--root", str(history), "--capture", "local", source="history")
    event(
        binary,
        "beforeSubmitPrompt",
        agent="cursor",
        conversation_id="cursor-native",
        prompt="kept",
    )
    for name in order:
        result = event(
            binary,
            name,
            agent="cursor",
            conversation_id="cursor-native",
            text="PRIVATE",
            status="completed",
        )
    assert result["disposition"] == "recorded"
    assert b"kept" in contents(history) and b"PRIVATE" not in contents(history)


def test_explicit_shared_capture_filters_the_http_payload(binary: Binary) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        # One shared source is explicit; both profile and binding consent to it.
        definition = install(
            binary,
            PROFILE
            + f'\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\nprefix = "{PREFIX}"\n[capture]\nmode = "shared"\nreplacements = [{{from = "SCRUBME", to = "removed"}}]\n',
        )["definition"]
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
        event(binary, "UserPromptSubmit", env=credentials, prompt="shared SCRUBME")
        done = event(binary, "Stop", env=credentials, last_assistant_message="answer")
        assert done["disposition"] == "pending"
        done["conversation_id"] = done["session_id"]
        turn = landed(server, done["conversation_id"])[0]
        assert "SCRUBME" not in json.dumps(turn)
        assert turn["messages"][0]["content"] == "shared removed"
        assert "native-session" in turn["actor"]
        document = server.post(
            "memory/read",
            scope="workspace:portable",
            path="sessions/" + done["conversation_id"],
        )["document"]
        assert "SCRUBME" not in json.dumps(document)
        policy_id = hashlib.sha256(
            json.dumps(definition, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        assert "capture_policy: " + policy_id in document["content"]


def test_local_history_stays_local_with_remote_knowledge(binary: Binary) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        install(binary, capture_profile("local"))
        bind(binary, "--url", server.url, "--token-env", "WORKSPACE_TOKEN")
        local = binary.directory / "private"
        bind(binary, "--root", str(local), "--capture", "local", source="history")
        credentials = {"WORKSPACE_TOKEN": "reader-token"}
        event(binary, "UserPromptSubmit", env=credentials, prompt="LOCAL-ONLY")
        done = event(
            binary, "Stop", env=credentials, last_assistant_message="private reply"
        )
        # The knowledge source is remote, so the Stop does not land inline.
        assert done["disposition"] == "pending"
        until(lambda: b"private reply" in contents(local), "the local turn")
        assert b"LOCAL-ONLY" in contents(local)
        assert server.post("store/conversations") == {"conversations": []}
        assert server.post("store/scopes") == {"scopes": []}


def test_identity_is_stable_and_distinct_across_installations_and_clients(
    binary: Binary,
) -> None:
    ids = []
    for machine in ["work", "home"]:
        directory = binary.directory / machine
        directory.mkdir()
        client = Binary(binary.executable, directory)
        install(client)
        bind(client, "--root", str(directory / "data"))
        for agent in ["codex", "claude-code"]:
            first = event(
                client,
                "UserPromptSubmit",
                agent=agent,
                session_id="x" * 128,
                prompt="no capture",
            )
            again = event(client, "Stop", agent=agent, session_id="x" * 128)
            assert first["session_id"] == again["session_id"]
            assert len(first["session_id"]) <= 128
            installation = tomllib.loads(
                (client.home / "profile-bindings.toml").read_text()
            )["installation_id"]
            canonical = json.dumps(
                [installation, agent, "x" * 128], separators=(",", ":")
            ).encode()
            assert first["session_id"] == PREFIX + hashlib.sha256(canonical).hexdigest()
            ids.append(first["session_id"])
    assert len(set(ids)) == 4


def test_credential_rotation_cannot_redirect_a_pending_span(binary: Binary) -> None:
    install(
        binary,
        PROFILE
        + f'\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\nprefix = "{PREFIX}"\n[capture]\nmode = "shared"\n',
    )
    bind(
        binary,
        "--url",
        "https://unreachable.invalid",
        "--token-env",
        "CAPTURE_TOKEN",
        "--capture",
        "shared",
    )
    event(
        binary,
        "UserPromptSubmit",
        env={"CAPTURE_TOKEN": "old-synthetic-token"},
        prompt="retained",
    )
    spool = binary.home / "profile-spool"
    (span,) = spool.glob("*.jsonl")
    retained = span.read_bytes()
    with slots_held(binary.home):
        result = run(
            binary,
            "record",
            "--profile",
            "portable",
            "--agent",
            "codex",
            "--json",
            env={"CAPTURE_TOKEN": "new-synthetic-token"},
            stdin=json.dumps(
                {
                    "session_id": "native-session",
                    "hook_event_name": "Stop",
                    "last_assistant_message": "no outbound request",
                }
            ),
        )
    # #447, ruling D5: the span recorded under the old credential is held, never
    # sent under the new one. The reply recorded under the new credential is
    # queued for a lander (none starts while the test holds both slots).
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["disposition"] == "pending"
    held, pending = spool / "held" / span.stem, spool / "pending" / span.stem
    assert (held / "1.jsonl").read_bytes().endswith(retained)
    assert json.loads((held / "1.reason").read_text()) == {"reason": "policy_changed"}
    queued = (pending / "2.jsonl").read_bytes()
    assert b"no outbound request" in queued and b"retained" not in queued
    old, new = (
        json.loads(state.read_text())
        for state in [held / "1.state", pending / "2.state"]
    )
    assert old["fingerprint"] != new["fingerprint"]
    assert b"synthetic-token" not in contents(spool)


@pytest.mark.parametrize("agent", ["codex", "claude-code", "cursor", "muse-code", "pi"])
def test_small_context_budget_keeps_identity_and_counts_mcp_instructions(
    binary: Binary, agent: str
) -> None:
    install(binary, PROFILE + "\n[context]\nmax_chars = 512\n")
    bind(binary, "--root", str(binary.directory / "data"))
    payload: dict[str, Any] = (
        {"conversation_id": "cursor-native"} if agent == "cursor" else {}
    )
    context = event(binary, "SessionStart", agent=agent, **payload)["context"]
    instructions = rpc(binary, actor=f"mcp:{agent}")[0]["result"]["instructions"]
    assert PREFIX in context
    assert len(context) + len(instructions) <= 512
    assert "omitted" in context
