"""The "where we left off" block at session start and after a compaction,
from light records on a server that keeps them and from whole turns on one
that does not (DESIGN §21.7, §33 and §40.54 item 19, #448; the T1fd slice).

The two turns of `continuing.py` are written as records to a
records-enabled FileStore behind a server and whole to a plain one, each
with the same sessions document. The real `SessionStart` hook asks both
through a proxy that keeps each request: the block is the literal composed
here from the frame's released wording, the same on both servers but for
the actor each answers; the records server is read with `read_records`
only, its headers first and then through the last turn they named, and the
plain one with `read_turns`. A start whose records read fails still tells
its recording notices, alone. Of four turns holding 1.5 MiB results the
newest two are shown from heads alone, within the block's read window, and
a profile over HTTP starts from its history's records.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.contract.landing.test_notices import HEADER, drops
from tests.contract.profile_support import PREFIX, PROFILE, bind, grant_env, install
from tests.contract.records.continuing import (
    CONVERSATION,
    SCOPE,
    ends,
    records_of,
    store,
)
from tests.contract.records.recalling import capture
from tests.contract.records.support import enable, write
from tests.contract.records.test_continue import history
from tests.contract.support import Binary, Server, serve
from tests.contract.test_profile_capture import event, landed, until

OPEN = (
    "[where we left off — the sessions recorded in this scope, newest first, "
    "log-projected and read-only; the numbers in square brackets are turn "
    "references]"
)
CLOSE = (
    '[end — call recall_turn(n, conversation="<id>") on the neosian server to '
    "re-read a listed turn verbatim, or search_history(query) there to find a "
    "turn of any session by its words; [link <id>:N] stands for a URL, path or "
    "id of that conversation — recall the turn to read it]"
)


def block(actor: str) -> str:
    return "\n".join(
        [
            OPEN,
            f"[conversation {CONVERSATION} — written by {actor}]",
            "[1] USER: Fix the parser | AGENT: Reading it. | "
            'TOOL Read({"file_path":"/w/a.py"}) → def parse(): pass | AGENT: Fixed.',
            '[2] USER: Run the tests | TOOL Bash({"command":"pytest -q"}) → 3 passed'
            " | AGENT: All green.",
            CLOSE,
        ]
    )


def listed(server: Server, conversation: str = CONVERSATION) -> None:
    content = f"# codex session {conversation}\n\n- turns: 2\n"
    path = f"sessions/{conversation}"
    server.post("memory/write", scope=SCOPE, path=path, content=content)


def start(
    binary: Binary, url: str, token: str, session: str, source: str
) -> subprocess.CompletedProcess[str]:
    """The SessionStart hook for `url`."""
    payload = {"session_id": session, "hook_event_name": "SessionStart"}
    return subprocess.run(
        [str(binary.executable), "record", "--url", url, "--scope", SCOPE, "--json"],
        input=json.dumps({**payload, "source": source}),
        env={**binary.environment(), "NEOSIAN_CLIENT_TOKEN": token},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def left_off(started: subprocess.CompletedProcess[str]) -> str:
    assert started.returncode == 0, started.stdout + started.stderr
    context = str(json.loads(started.stdout)["context"])
    return OPEN + context.split(OPEN, 1)[1].split(CLOSE, 1)[0] + CLOSE


def test_startup_and_compaction_show_the_same_block_from_records_and_turns(
    binary: Binary, tmp_path: Path
) -> None:
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    with serve(binary) as kept, serve(plain) as legacy:
        store(kept, legacy)
        for name, server in (("kept", kept), ("legacy", legacy)):
            listed(server)
            actor = ends(server, name == "kept")[0]
            with capture(server) as held:
                shown = [
                    left_off(start(binary, held.url, server.token, *own))
                    for own in (("s-new", "startup"), (CONVERSATION, "compact"))
                ]
            assert shown == [block(actor)] * 2, name
            routes = history(held.exchanges)
            names = {route for route, _ in routes}
            if name == "kept":
                expected_routes = {
                    "last_turn_number",
                    "read_records",
                    "read_projections",
                }
                assert names == expected_routes, names
                reads = [body for route, body in routes if route == "read_records"]
                assert len(reads) == 2 * 2, reads
                for header, viewed in zip(reads[::2], reads[1::2], strict=True):
                    assert header == {
                        "conversation_id": CONVERSATION,
                        "through": 2,
                        "kinds": [],
                    }
                    assert viewed["through"] == 2 and "after" not in viewed
                    assert "reasoning" not in viewed["kinds"]
                    assert (
                        viewed["heads"]["attachment"] == viewed["heads"]["extra"] == 1
                    )
            else:
                assert names == {"read_turns", "read_projections"}, names


def test_a_start_whose_records_read_fails_still_tells_its_notices(
    binary: Binary,
) -> None:
    enable(binary)
    with serve(binary) as kept:
        store(kept, kept)
        listed(kept)
        stamp = "2026-11-20T09:00:00.000000Z"
        drops(binary, 2, 4096, stamp, stamp)
        with capture(kept, fail="read_records") as held:
            told = start(binary, held.url, kept.token, "s-notices", "startup")
            again = start(binary, held.url, kept.token, "s-notices", "startup")
        assert told.returncode == 0, told.stderr
        assert json.loads(told.stdout)["context"] == (
            f"{HEADER}\nTo stay within 512 MiB on this machine, neosian deleted the "
            "oldest turns that had not been recorded: 2 turns (4.0 KiB)."
        )
        assert told.stderr.startswith("error: ") and "injected" in told.stderr
        assert again.returncode == 1, "nothing left to tell: the failure as before"


def test_large_results_are_shown_from_heads_of_the_newest_turns(
    binary: Binary,
) -> None:
    """Four turns of 1.5 MiB results: the block reads at most 4 MiB of
    stored turns, so the newest two, and folds the first two unread."""
    result = "HEAD " + "z " * (3 << 17) + "MIDDLE " + "y " * (3 << 17) + "TAIL"
    enable(binary)
    with serve(binary) as kept:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "show the log"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "r", "name": "Bash", "arguments": {"cmd": "cat"}}
                ],
            },
            {"role": "tool", "content": result, "tool_call_id": "r"},
            {"role": "assistant", "content": "It is long."},
        ]
        items = records_of(messages)
        at = next(n for n, item in enumerate(items) if item["kind"] == "tool_result")
        chunks = [result[n : n + 60_000] for n in range(0, len(result), 60_000)]
        pieces = items[:at] + [
            {
                **items[at],
                "seq": at + n,
                "chunk": n,
                "text": chunk,
                "content": None if n else items[at]["content"],
            }
            for n, chunk in enumerate(chunks)
        ]
        pieces += [
            {**item, "seq": at + len(chunks) + n}
            for n, item in enumerate(items[at + 1 :])
        ]
        for number in range(1, 5):
            for first in range(0, len(pieces), 32):
                part = pieces[first : first + 32]
                seal = first + 32 >= len(pieces)
                write(kept, "left-big", f"d-{number}", part, seal=seal)
        listed(kept, "left-big")
        with capture(kept) as held:
            shown = left_off(start(binary, held.url, kept.token, "s-big", "startup"))
        fold = "\n[1-2] 2 earlier turns not shown\n[3] USER: show the log | TOOL Bash"
        assert fold in shown, shown
        assert "[4] USER: show the log | TOOL Bash" in shown
        assert "HEAD" in shown and "TAIL" in shown and "MIDDLE" not in shown
        reads = [
            (json.loads(body), size)
            for _, path, body, size in held.exchanges
            if path.endswith("read_records")
        ]
        assert [body.get("after") for body, _ in reads] == [None, 2], reads
        assert reads[1][0]["through"] == 4
        assert sum(size for _, size in reads) < 2 * 2 * 8_192 + 16_384, reads


def test_a_profile_over_http_starts_from_its_history_records(binary: Binary) -> None:
    """A profile whose history source is a records server behind the proxy:
    the start reads that source's records, never its whole turns."""
    env = grant_env(binary.directory / "grants.toml")
    enable(binary)
    section = (
        f'\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\n'
        f'prefix = "{PREFIX}"\n[capture]\nmode = "shared"\n'
    )
    credentials = {"CAPTURE_TOKEN": "contract-admin"}
    with serve(binary, extra_env=env) as server, capture(server) as held:
        install(binary, PROFILE + section)
        token = ("--token-env", "CAPTURE_TOKEN", "--capture", "shared")
        bind(binary, "--url", held.url, *token)
        event(binary, "UserPromptSubmit", env=credentials, prompt="profiled")
        done = event(binary, "Stop", env=credentials, last_assistant_message="answer")
        conversation = done["session_id"]
        landed(server, conversation)
        path = f"sessions/{conversation}"
        read = lambda: server.post(  # noqa: E731
            "memory/read", scope="workspace:portable", path=path
        )["document"]
        until(read, "the sessions document")
        held.exchanges.clear()
        context = event(binary, "SessionStart", env=credentials, source="startup")
        names = {route for route, _ in history(held.exchanges)}
    shown = str(context["context"])
    assert f"[conversation {conversation} — written by " in shown, shown
    assert "\n[1] USER: profiled | AGENT: answer\n" in shown, shown
    assert "read_records" in names and "read_turns" not in names, names
