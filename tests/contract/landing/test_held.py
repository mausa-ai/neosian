"""Held turns listed, discarded and put back (DESIGN §40.54 item 17.6; the
T1e2a slice and the "Held selectors" ruling of the T1e2 plan). Turns are
held by real refusals: queued for a URL by the real Stop hook while the
test holds the conversation's landing lock, refused by a forwarding stub in
front of a real `neosian serve`, and landed by `neosian record-land` in the
foreground. `neosian record held` lists them and the pending turns this
environment does not serve, deletes one or all, and puts one back, which
the next lander lands behind a turn queued meanwhile. A turn held because
its profile's policy changed goes back only once that policy applies
again. Expected values are literal or computed here from the files."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, Serve
from tests.contract.landing.lander import (
    draft_id,
    enable,
    environment,
    land,
    queue,
    served_root,
    turns,
)
from tests.contract.landing.stub import Rule, Stub, forwarding, route_is
from tests.contract.landing.test_queue import CHANGE, event, land as land_profile
from tests.contract.profile_support import install, run
from tests.contract.support import Binary, Server
from tests.contract.test_profile_capture import capture_profile, contents
from tests.contract.test_profile_pause import local

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")
WRITE = "conversation/write_records"
APPEND = "conversation/append_turn"
UNREADABLE = "a 2xx was answered but could not be read; it may be stored"

pytestmark = POSIX


@pytest.fixture
def remote(binary: Binary, port: int, serve_on: Serve) -> Iterator[tuple[Server, Stub]]:
    enable(binary, served_root(binary))
    with serve_on(port) as server, forwarding(server.url) as stub:
        yield server, stub


def refuse(stub: Stub, times: int) -> None:
    """The next `times` parts that carry records are refused with a 400."""
    body = {"error": {"code": "value_error", "message": "refused here", "details": {}}}
    stub.add(
        Rule(
            when=lambda e: e.route == WRITE and bool(e.body["records"]),
            kind="answer",
            status=400,
            body=body,
            times=times,
        )
    )


def held_file(binary: Binary, name: str, part: str = "jsonl") -> Path:
    conversation, n = name.rsplit("/", 1)
    return binary.home / "spool" / "held" / conversation / f"{n}.{part}"


def closed_at(path: Path) -> str:
    return str(json.loads(path.with_suffix(".state").read_text())["closed_at"])


def held(binary: Binary, *args: str, env: dict[str, str] | None = None) -> Any:
    """`neosian record held ARGS --json`: its exit and its object."""
    done = run(binary, "record", "held", *args, "--json", env=env)
    return done.returncode, json.loads(done.stdout)


def text(binary: Binary, *args: str) -> list[str]:
    done = run(binary, "record", "held", *args)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.splitlines()


def test_held_lists_a_refused_turn_and_a_turn_this_environment_does_not_serve(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    _, stub = remote
    n = queue(binary, hold_lock, stub.url, "held-list", "refused")
    refuse(stub, 1)
    assert land(binary)["held"] == 1
    name = f"held-list/{n}"
    turn = held_file(binary, name)
    w = queue(binary, hold_lock, "http://127.0.0.1:9", "held-wait", "waits")
    waiting = binary.home / "spool" / "pending" / "held-wait" / f"{w}.jsonl"
    spool = str(binary.home / "spool")
    host = stub.url.split("://", 1)[1]
    listed = {
        "id": name,
        "spool": spool,
        "closed_at": closed_at(turn),
        "bytes": turn.stat().st_size,
        "destination": host,
        "reason": "refused",
        "status": 400,
        "code": "value_error",
        "message": "refused here",
    }
    unserved = {
        "id": f"held-wait/{w}",
        "spool": spool,
        "closed_at": closed_at(waiting),
        "bytes": waiting.stat().st_size,
        "destination": "127.0.0.1:9",
    }
    # Without the token nothing here serves the URL's turn; with it, it is
    # served, and only the held turn is listed.
    assert held(binary) == (0, {"held": [listed], "unserved": [unserved]})
    assert held(binary, env=environment(binary)) == (
        0,
        {"held": [listed], "unserved": []},
    )
    assert text(binary) == [
        f"Held turns (1, {listed['bytes']} B); nothing sends them again by itself:",
        f"  {name}  closed {listed['closed_at']}  {listed['bytes']} B  "
        "refused (400 value_error): refused here",
        f"Pending turns this environment does not serve (1, {unserved['bytes']} B); "
        "a hook whose environment names their destination lands them:",
        f"  held-wait/{w}  closed {unserved['closed_at']}  {unserved['bytes']} B  "
        "for 127.0.0.1:9",
    ]
    # Metadata only: the turn's words appear nowhere in the listing.
    assert "refused done" not in "\n".join(text(binary))


def test_a_discard_deletes_one_or_all_and_reports_turns_and_bytes(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    _, stub = remote
    first = queue(binary, hold_lock, stub.url, "held-a", "one")
    second = queue(binary, hold_lock, stub.url, "held-a", "two")
    other = queue(binary, hold_lock, stub.url, "held-b", "three")
    refuse(stub, 3)
    assert land(binary)["held"] == 3
    names = [f"held-a/{first}", f"held-a/{second}", f"held-b/{other}"]
    sizes = [held_file(binary, name).stat().st_size for name in names]
    spool = str(binary.home / "spool")
    assert held(binary, "--discard", names[0]) == (
        0,
        {
            "action": "discard",
            "turns": [
                {
                    "id": names[0],
                    "spool": spool,
                    "outcome": "discarded",
                    "bytes": sizes[0],
                    "queued": None,
                    "why": None,
                }
            ],
            "done": {"turns": 1, "bytes": sizes[0]},
        },
    )
    for part in ("jsonl", "state", "reason"):
        assert not held_file(binary, names[0], part).exists()
    assert all(held_file(binary, name).is_file() for name in names[1:])
    assert text(binary, "--discard", "--all") == [
        f"{names[1]}: discarded ({sizes[1]} B)",
        f"{names[2]}: discarded ({sizes[2]} B)",
        f"Discarded 2 turns, {sizes[1] + sizes[2]} B.",
    ]
    assert held(binary) == (0, {"held": [], "unserved": []})
    missing = run(binary, "record", "held", "--discard", names[0])
    assert missing.returncode == 1
    assert f"error: no held turn {names[0]} in {spool}, " in missing.stderr


def test_a_refused_turn_put_back_lands_behind_a_turn_queued_meanwhile(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "held-retry"
    n = queue(binary, hold_lock, stub.url, session, "first")
    refuse(stub, 1)
    assert land(binary)["held"] == 1
    turn = held_file(binary, f"{session}/{n}")
    kept, state = turn.read_bytes(), json.loads(turn.with_suffix(".state").read_text())
    later = queue(binary, hold_lock, stub.url, session, "later")
    done, answer = held(binary, "--retry", f"{session}/{n}")
    queued = f"{session}/{later + 1}"
    assert (done, answer["turns"][0]["outcome"]) == (0, "retried")
    assert answer["turns"][0]["queued"] == queued
    assert answer["done"] == {"turns": 1, "bytes": len(kept)}
    pending = binary.home / "spool" / "pending" / session
    # Its bytes, close time and draft as they were; its old files gone.
    assert (pending / f"{later + 1}.jsonl").read_bytes() == kept
    assert json.loads((pending / f"{later + 1}.state").read_text()) == state
    assert state["draft"] == draft_id(session, kept.decode().split("\n", 1)[0])
    assert not turn.exists() and not turn.with_suffix(".reason").exists()
    # Nothing was sent by the retry; the lander lands both, in queue order.
    assert [t["messages"][0]["content"] for t in turns(server, session)] == []
    assert land(binary)["landed"] == 2
    prompts = [t["messages"][0]["content"] for t in turns(server, session)]
    assert prompts == ["later", "first"]


def test_a_turn_whose_answer_may_have_stored_it_goes_back_only_when_asked(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    server, stub = remote
    session = "held-maybe"

    def without_records(capabilities: Any) -> Any:
        capabilities.pop("turn_records", None)
        return capabilities

    stub.add(
        Rule(
            when=route_is("capabilities"),
            kind="rewrite",
            rewrite=without_records,
            times=99,
        )
    )
    n = queue(binary, hold_lock, stub.url, session, "maybe stored")
    stub.add(Rule(when=route_is(APPEND), kind="answer", status=200, body={"turn": 3}))
    assert land(binary)["held"] == 1
    name = f"{session}/{n}"
    done, answer = held(binary, "--retry", name)
    assert done == 1
    (row,) = answer["turns"]
    assert (row["outcome"], row["bytes"], row["queued"]) == (
        "may_be_stored",
        None,
        None,
    )
    assert "--allow-duplicate" in row["why"]
    assert held_file(binary, name).is_file()
    size = held_file(binary, name).stat().st_size
    assert text(binary, "--retry", name, "--allow-duplicate") == [
        f"{name}: put back as {session}/{n + 1} ({size} B)",
        f"Put back 1 turn, {size} B. Nothing is sent now: the next hook event that "
        "starts a lander for a turn's destination lands it.",
    ]
    assert land(binary)["landed"] == 1
    assert [t["messages"][0]["content"] for t in turns(server, session)] == [
        "maybe stored"
    ]


def test_a_turn_named_in_two_spool_trees_is_refused_until_one_is_named(
    binary: Binary, hold_lock: Hold, remote: tuple[Server, Stub]
) -> None:
    _, stub = remote
    n = queue(binary, hold_lock, stub.url, "held-twice", "twice")
    refuse(stub, 1)
    assert land(binary)["held"] == 1
    name = f"held-twice/{n}"
    copy = binary.home / "profile-spool" / "held" / "held-twice"
    shutil.copytree(held_file(binary, name).parent, copy)
    refused = run(binary, "record", "held", "--discard", name)
    assert refused.returncode == 1
    assert "is held in more than one spool tree" in refused.stderr
    assert "name one with --spool" in refused.stderr
    assert held_file(binary, name).is_file() and (copy / f"{n}.jsonl").is_file()
    tree = str(binary.home / "profile-spool")
    done, answer = held(binary, "--discard", name, "--spool", tree)
    assert (done, answer["turns"][0]["spool"]) == (0, tree)
    assert held_file(binary, name).is_file() and not (copy / f"{n}.jsonl").exists()
    names = [line.split("  ")[1] for line in text(binary)[1:]]
    assert names == [name]


def test_a_turn_held_for_its_policy_goes_back_once_that_policy_applies_again(
    binary: Binary,
) -> None:
    profile = capture_profile("local")
    history = local(binary, profile)
    conversation = event(binary, "UserPromptSubmit", prompt="before the change")[
        "session_id"
    ]
    spool = binary.home / "profile-spool"
    (guard,) = spool.glob("*.policy")
    recorded_under = guard.read_text()
    install(binary, profile + CHANGE)
    assert event(binary, "Stop", last_assistant_message="after")["turn"] == 1
    name = f"{conversation}/1"
    retry = ("--retry", name, "--profile", "portable")
    done, answer = held(binary, *retry)
    assert (done, answer["turns"][0]["outcome"]) == (1, "policy_differs")
    assert (spool / "held" / conversation / "1.jsonl").is_file()
    install(binary, profile)
    done, answer = held(binary, *retry)
    assert (done, answer["turns"][0]["queued"]) == (0, f"{conversation}/2")
    state = json.loads((spool / "pending" / conversation / "2.state").read_text())
    assert state["fingerprint"] == recorded_under
    assert state["destination"] == {
        "kind": "profile",
        "name": "portable",
        "project": str(binary.directory.resolve()),
        "source": "history",
    }
    assert (state["scope"], state["mount"]) == ("workspace:history", "history")
    assert "- source_session: native-session\n" in state["provenance"]
    assert b"before the change" not in contents(history)
    assert land_profile(binary)["landed"] == 1
    assert b"before the change" in contents(history)
