"""The detached lander (DESIGN §40.54 items 17.1 and 17.3; the T1e1d slice
of the T1e1 plan, rulings D4, D6, D11, D15 and E12). Hooks here are free to
start landers; every wait is a poll of an end state with a deadline. A Stop
for a URL answers `pending` and returns while its lander is held at the
forwarding stub, its output read to the end (the lander keeps no stream of
the hook open); the lander survives the hook's process group being killed,
leads its own session and group, and exits when nothing is landable. With
both slots held no lander starts; three concurrent Stops run at most two;
PostToolUse never starts one, UserPromptSubmit does for its queued
conversation. A small local turn lands inline, a larger one and one behind
an older queued turn wait for the lander and land in order. A paused
conversation is not landed. A lander killed at a held request, found by the
pid in its slot, is resumed by the next hook's lander without resending an
accepted part. A pause given while a part is in flight stops the turn
before its next part (ruling E19); one given while a seal is in flight
lets that turn finish. The OpenCode plugin's and Pi extension's own spawn
code leave a lander that outlives Node."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve, slot_files, slots_held
from tests.contract.landing.lander import (
    enable,
    environment,
    hook,
    pending,
    plant,
    popen,
    prompts,
    served_root,
    turns,
)
from tests.contract.landing.stub import (
    Exchange,
    Gate,
    Rule,
    Stub,
    forwarding,
    resumed_without_resending,
)
from tests.contract.profile_support import PREFIX, PROFILE, bind, grant_env, install
from tests.contract.support import ADMIN, Binary, Server, serve
from tests.contract.test_profile_capture import event as profile_event
from tests.contract.test_profile_pause import local

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="setsid and flock")
WRITE = "conversation/write_records"
LARGE = 1_000_000
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def until(check: Callable[[], object], what: str, seconds: float = 30.0) -> None:
    """Polls `check` until it holds, failing after `seconds`."""
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def landers(home: Path) -> list[int]:
    """The pids of `record-land --home HOME` processes alive now."""
    table = subprocess.run(
        ["ps", "-A", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    real, found = os.path.realpath(home), []
    for line in table.splitlines():
        pid, _, command = line.strip().partition(" ")
        words = command.split()
        if "record-land" in words and "--home" in words:
            at = words.index("--home") + 1
            if at < len(words) and os.path.realpath(words[at]) == real:
                found.append(int(pid))
    return found


def row(pid: int) -> dict[str, int]:
    """The process's group and parent, as `ps` shows them."""
    shown = subprocess.run(
        ["ps", "-o", "pid=,pgid=,ppid=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return dict(zip(("pid", "pgid", "ppid"), map(int, shown), strict=True))


def slot_pid(binary: Binary) -> int:
    """The pid the first busy slot names."""
    for slot in slot_files(binary.home):
        text = slot.read_text().strip() if slot.exists() else ""
        if text:
            return int(text)
    raise AssertionError("no slot names a lander")


def holding(stub: Stub, when: Callable[[Exchange], bool] | None = None) -> Gate:
    """Every matching `write_records` request held until released, then
    forwarded and answered."""
    gate = Gate()
    match = when or (lambda e: e.route == WRITE)
    stub.add(Rule(when=match, kind="hold", gate=gate, times=99))
    return gate


def stop(
    binary: Binary, url: str, session: str, **options: Any
) -> subprocess.CompletedProcess[str]:
    stopped = hook(
        binary, url, session, "Stop", last_assistant_message="done", **options
    )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert json.loads(stopped.stdout)["disposition"] == "pending"
    return stopped


def test_a_stop_returns_while_its_lander_is_held_and_the_turn_lands_after(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "lander-held"
    gate = holding(stub)
    plant(binary, session, "slow destination", LARGE)
    # The hook's stdout and stderr are pipes read to their end: the call
    # returns only once nothing holds them open, the lander included.
    stop(binary, stub.url, session, starts=True)
    until(gate.reached.is_set, "the lander held at the stub")
    assert pending(binary, session) == [1] and turns(server, session) == []
    assert len(landers(binary.home)) == 1
    gate.release()
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["slow destination"]
    until(lambda: not landers(binary.home), "the lander exited")


def test_the_lander_outlives_the_hook_s_process_group(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "lander-group"
    gate = holding(stub)
    plant(binary, session, "the group is killed", LARGE)
    payload = json.dumps(
        {
            "session_id": session,
            "hook_event_name": "Stop",
            "last_assistant_message": "x",
        }
    )
    command = [str(binary.executable), "record", "--url", stub.url]
    command += ["--scope", "user:contract", "--json"]
    # A hook runner's shell in a session of its own, which stays after the
    # hook, as a client's process group does.
    shell = subprocess.Popen(
        ["/bin/sh", "-c", 'printf %s "$0" | "$@"; echo; exec sleep 60', payload]
        + command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment(binary),
        cwd=binary.directory,
        start_new_session=True,
        text=True,
    )
    try:
        assert shell.stdout is not None
        assert json.loads(shell.stdout.readline())["disposition"] == "pending"
        until(gate.reached.is_set, "the lander held at the stub")
        lander = slot_pid(binary)
        shown = row(lander)
        print(f"lander {shown}, hook group {shell.pid}")
        assert shown["pgid"] == lander != shell.pid
        assert os.getsid(lander) == lander
        assert shown["ppid"] != shell.pid
        os.killpg(shell.pid, signal.SIGKILL)
        shell.wait(timeout=10)
        assert landers(binary.home) == [lander]
    finally:
        if shell.poll() is None:
            shell.kill()
            shell.wait(timeout=10)
    gate.release()
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["the group is killed"]


def test_with_both_slots_held_no_lander_starts_and_a_prompt_starts_one(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "lander-slots"
    plant(binary, session, "waits for a slot")
    with slots_held(binary.home):
        stop(binary, stub.url, session, starts=True)
        assert landers(binary.home) == []
    assert stub.records() == [] and pending(binary, session) == [1]
    # UserPromptSubmit of a conversation with a queued turn starts one (D15).
    opened = hook(
        binary, stub.url, session, "UserPromptSubmit", starts=True, prompt="n"
    )
    assert opened.returncode == 0, opened.stderr
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["waits for a slot"]


def test_three_concurrent_stops_run_at_most_two_landers(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    sessions = ["lander-three-a", "lander-three-b", "lander-three-c"]
    gate = holding(stub)
    for session in sessions:
        plant(binary, session, session)
    results: list[subprocess.CompletedProcess[str]] = []

    def stopped(session: str) -> None:
        results.append(
            hook(
                binary,
                stub.url,
                session,
                "Stop",
                starts=True,
                last_assistant_message="x",
            )
        )

    threads = [threading.Thread(target=stopped, args=(s,)) for s in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert [json.loads(r.stdout)["disposition"] for r in results] == ["pending"] * 3

    def held() -> set[str]:
        return {e.body["conversation_id"] for e in stub.records()}

    until(lambda: len(held()) == 2, "two landers held at the stub")
    # A third lander, if one was spawned, finds no slot and exits at once.
    until(lambda: len(landers(binary.home)) <= 2, "at most two landers")
    assert len(landers(binary.home)) == 2 and len(held()) == 2
    gate.release()
    for session in sessions:

        def landed(session: str = session) -> bool:
            return not pending(binary, session)

        until(landed, f"{session} landed")
        assert prompts(server, session) == [session]
    until(lambda: not landers(binary.home), "the landers exited")


def test_post_tool_use_never_starts_a_lander(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "lander-tool"
    plant(binary, session, "queued before a tool round")
    stop(binary, stub.url, session)
    gate = holding(stub)
    used = hook(
        binary,
        stub.url,
        session,
        "PostToolUse",
        starts=True,
        tool_name="Bash",
        tool_use_id="t",
        tool_input={"command": "ls"},
        tool_response="ok",
    )
    assert used.returncode == 0, used.stderr
    # A lander started by the hook would be alive now: it waits at the stub.
    assert landers(binary.home) == [] and stub.records() == []
    opened = hook(
        binary, stub.url, session, "UserPromptSubmit", starts=True, prompt="p"
    )
    assert opened.returncode == 0, opened.stderr
    until(gate.reached.is_set, "the prompt's lander held at the stub")
    gate.release()
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["queued before a tool round"]


def local_hook(
    binary: Binary, session: str, name: str, **payload: Any
) -> dict[str, Any]:
    result = subprocess.run(
        [str(binary.executable), "record", "--scope", "user:contract", "--json"],
        input=json.dumps({"session_id": session, "hook_event_name": name, **payload}),
        env=binary.environment(),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dict(json.loads(result.stdout))


def stored(binary: Binary, session: str) -> list[str]:
    log = binary.home / "conversations" / session / "turns.jsonl"
    if not log.exists():
        return []
    return [
        json.loads(r)["messages"][0]["content"] for r in log.read_text().splitlines()
    ]


def test_a_small_local_turn_lands_inline_and_a_larger_or_later_one_waits(
    binary: Binary, hold_lock: Hold
) -> None:
    session = "lander-local"
    local_hook(binary, session, "UserPromptSubmit", prompt="small")
    small = local_hook(binary, session, "Stop", last_assistant_message="ok")
    assert (small["disposition"], small["turn"]) == ("recorded", 1)
    local_hook(binary, session, "UserPromptSubmit", prompt="large")
    local_hook(
        binary,
        session,
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="t",
        tool_input={"command": "cat"},
        # Over the 128 KiB threshold, under the design's 1 MiB (O3).
        tool_response="y" * 200_000,
    )
    large = local_hook(binary, session, "Stop", last_assistant_message="ok")
    assert (large["disposition"], large["turn"]) == ("pending", None)
    until(lambda: stored(binary, session) == ["small", "large"], "the large turn")
    pending_dir = binary.home / "spool" / "pending" / session
    local_hook(binary, session, "UserPromptSubmit", prompt="older")
    with slots_held(binary.home), hold_lock(pending_dir):
        older = local_hook(binary, session, "Stop", last_assistant_message="ok")
    assert older["disposition"] == "pending"
    local_hook(binary, session, "UserPromptSubmit", prompt="later")
    later = local_hook(binary, session, "Stop", last_assistant_message="ok")
    # Behind an older queued turn: the lander lands both, in order (D6).
    assert (later["disposition"], later["turn"]) == ("pending", None)
    until(lambda: len(stored(binary, session)) == 4, "both turns")
    assert stored(binary, session) == ["small", "large", "older", "later"]


def test_a_paused_conversation_is_not_landed(binary: Binary, hold_lock: Hold) -> None:
    history = local(binary)
    with slots_held(binary.home):
        conversation = profile_event(binary, "UserPromptSubmit", prompt="private")[
            "session_id"
        ]
        spool = binary.home / "profile-spool"
        with hold_lock(spool / "pending" / conversation):
            queued = profile_event(binary, "Stop", last_assistant_message="x")
    assert queued["disposition"] == "pending"
    # A pause cut after its marker, before its cleanup: the marker binds.
    (spool / f"{conversation}.paused").write_text("")
    landed = subprocess.run(
        [str(binary.executable), "record-land", "--home", str(binary.home)],
        env=binary.environment(),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    summary = json.loads(landed.stdout)
    assert (summary["landed"], summary["paused"]) == (0, [conversation])
    assert list((spool / "pending" / conversation).glob("*.jsonl"))
    assert not (history / "conversations").exists()


def test_a_pause_between_parts_sends_no_further_part_and_discards_the_draft(
    binary: Binary, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    paused, later = "lander-pause-a", "lander-pause-b"
    for session, size in ((paused, LARGE), (later, 0)):
        plant(binary, session, session, size)
        stop(binary, stub.url, session)
    gate = Gate()

    def first_part(e: Exchange) -> bool:
        return e.route == WRITE and bool(e.body["records"]) and not e.body.get("seal")

    stub.add(Rule(when=first_part, kind="hold", gate=gate))
    lander = popen(binary)
    until(gate.reached.is_set, "the first part held at the stub")
    # What `record pause` publishes first, while the part is in flight.
    (binary.home / "spool" / f"{paused}.paused").write_text("")
    gate.release()
    out, err = lander.communicate(timeout=60)
    assert lander.returncode == 0, err
    summary = json.loads(out)
    assert (summary["landed"], summary["paused"]) == (1, [paused])
    sent = [e.body for e in stub.records(paused)]
    parts = [b for b in sent if b["records"]]
    assert len(parts) == 1 and not parts[0].get("seal")
    assert sent[-1].get("discard") is True and stub.records(paused)[-1].status == 200
    assert turns(server, paused) == []
    directory = binary.home / "spool" / "pending" / paused
    assert not list(directory.glob("*.jsonl")) and not list(directory.glob("*.state"))
    assert prompts(server, later) == [later]


def test_a_seal_in_flight_finishes_its_turn_and_the_pause_discards_the_rest(
    binary: Binary,
) -> None:
    enable(binary, binary.home)
    grants = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=grants) as server, forwarding(server.url) as stub:
        history = '\n[history]\nsource = "knowledge"\nscope = "workspace:portable"'
        install(
            binary,
            PROFILE + history + f'\nprefix = "{PREFIX}"\n'
            '[capture]\nmode = "shared"\n',
        )
        bind(
            binary,
            "--url",
            stub.url,
            "--token-env",
            "CAPTURE_TOKEN",
            "--capture",
            "shared",
        )
        env = {"CAPTURE_TOKEN": ADMIN}
        with slots_held(binary.home):
            for prompt in ("sealed in flight", "not started"):
                opened = profile_event(
                    binary, "UserPromptSubmit", env=env, prompt=prompt
                )
                queued = profile_event(
                    binary, "Stop", env=env, last_assistant_message="a"
                )
                assert queued["disposition"] == "pending"
        conversation = opened["session_id"]
        gate = Gate()
        sealing = Rule(
            when=lambda e: e.route == WRITE and e.body.get("seal") is True,
            kind="hold",
            gate=gate,
        )
        stub.add(sealing)
        lander = subprocess.Popen(
            [str(binary.executable), "record-land", "--home", str(binary.home)],
            env={**binary.environment(), **env},
            cwd=binary.directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        until(gate.reached.is_set, "the seal held at the stub")
        assert binary.json("record", "pause", conversation)["disposition"] == (
            "capture_paused"
        )
        gate.release()
        out, err = lander.communicate(timeout=60)
        assert lander.returncode == 0, err
        summary = json.loads(out)
        assert (summary["landed"], summary["paused"]) == (1, [conversation])
        stored = server.post("conversation/read_turns", conversation_id=conversation)
        assert [t["messages"][0]["content"] for t in stored["turns"]] == [
            "sealed in flight"
        ]
        drafts = {e.body["draft"] for e in stub.records(conversation)}
        assert len(drafts) == 1, "the second turn was never started"
        spool = binary.home / "profile-spool" / "pending" / conversation
        assert not list(spool.glob("*.jsonl"))


KILLS = {
    "a part held before the server applied it": ("hold_before", False),
    "a part held after the server applied it": ("hold_after", False),
    "the seal applied, its answer dropped": ("hold_after", True),
}


@pytest.mark.parametrize("point", list(KILLS))
def test_a_detached_lander_killed_at_a_held_request_is_resumed_by_the_next_hook(
    binary: Binary, remote: tuple[Server, Stub], point: str
) -> None:
    server, stub = remote
    kind, sealing = KILLS[point]
    session = "lander-killed"
    gate = Gate()

    def first_part(e: Exchange) -> bool:
        if e.route != WRITE or not e.body["records"]:
            return False
        return bool(e.body.get("seal")) == sealing

    stub.add(Rule(when=first_part, kind=kind, gate=gate))
    plant(binary, session, "killed and resumed", LARGE)
    stop(binary, stub.url, session, starts=True)
    until(gate.reached.is_set, "the lander held at the stub")
    os.kill(slot_pid(binary), signal.SIGKILL)
    until(lambda: not landers(binary.home), "the killed lander gone")
    gate.release()
    assert pending(binary, session) == [1]
    stub.next_run()
    opened = hook(
        binary, stub.url, session, "UserPromptSubmit", starts=True, prompt="n"
    )
    assert opened.returncode == 0, opened.stderr
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["killed and resumed"]
    resumed_without_resending(stub.log)


def node_version() -> tuple[int, ...]:
    node = shutil.which("node")
    if node is None:
        return ()
    shown = subprocess.run([node, "--version"], capture_output=True, text=True)
    return tuple(int(part) for part in shown.stdout.strip().lstrip("v").split("."))


@pytest.mark.skipif(node_version() < (22, 19), reason="needs Node 22.19 or later")
@pytest.mark.parametrize("client", ["opencode", "pi"])
def test_the_node_clients_spawn_code_leaves_a_lander_that_outlives_node(
    binary: Binary, remote: tuple[Server, Stub], client: str
) -> None:
    server, stub = remote
    session = f"lander-{client}"
    gate = holding(stub)
    argv = [str(binary.executable), "record", "--url", stub.url]
    argv += ["--scope", "user:contract", "--agent", client]
    harness = ROOT / "tests" / "clients" / "lander_spawn.mjs"
    ran = subprocess.run(
        [
            "node",
            str(harness),
            client,
            session,
            json.dumps(argv),
            str(binary.directory),
        ],
        env=environment(binary),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (ran.returncode, ran.stdout) == (0, "stopped\n"), ran.stderr
    # Node has exited while the destination still holds the lander.
    assert not gate.released.is_set() and pending(binary, session) == [1]
    until(gate.reached.is_set, "the lander held at the stub")
    gate.release()
    until(lambda: not pending(binary, session), "the turn landed")
    assert prompts(server, session) == ["survive the client"]
