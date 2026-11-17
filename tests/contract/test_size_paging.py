"""Spooled answers and size paging through the binary's doors (S14, §40.46, #417).

Large answers are written item by item into a spool while the server holds the
store and sent, with their length, only after it has let go: a refusal found
anywhere is the 400 it always was, and a slow reader holds nothing of the
store. File searches keep one log open at a time. The binary's remote client
sends 2.16.0's requests to every server, size paging or not, and its doors
answer exactly as a local read does. Expected texts are literal.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tests.contract.profile_support import run
from tests.contract.support import ADMIN, Binary, Server, serve
from tests.contract.test_history_ranking import CORPUS, mcp, plant, user

CAP = "NEOSIAN_MAX_PAGE_BYTES"
LONG = "long-session"
REMOTE = {"NEOSIAN_CLIENT_TOKEN": ADMIN}


def plant_long(root: Path, count: int, size: int, bad: bool = False) -> None:
    folder = root / "conversations" / LONG
    folder.mkdir(parents=True)
    rows = [
        {
            "neosian_format": 1,
            "turn": number,
            "messages": [user(f"long dashboard turn {number} " + "word " * size)],
            "created_at": f"2026-11-07T09:{number // 60:02d}:{number % 60:02d}Z",
            "actor": "cli:fixture",
            "redacted": False,
        }
        for number in range(1, count + 1)
    ]
    if bad:
        rows.append({"neosian_format": 1, "turn": 0, "messages": []})
    text = "".join(json.dumps(row) + "\n" for row in rows)
    (folder / "turns.jsonl").write_text(text, encoding="utf-8")


def raw(server: Server, route: str, body: dict[str, Any]) -> tuple[int, bytes]:
    status, headers, answer = exchange(server, route, body)
    return status, answer


def exchange(
    server: Server, route: str, body: dict[str, Any]
) -> tuple[int, dict[str, str], bytes]:
    request = Request(
        f"{server.url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {ADMIN}"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
            return response.status, lowered(response.headers), response.read()
    except HTTPError as error:
        return error.code, lowered(error.headers), error.read()


def lowered(headers: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}


class SlowReader:
    """One request read a few bytes at a time on a raw socket, as a client on
    a slow link reads it; `read` counts what arrived."""

    def __init__(self, server: Server, route: str, body: dict[str, Any]) -> None:
        port = int(server.url.rsplit(":", 1)[1])
        data = json.dumps(body).encode()
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self.socket.connect(("127.0.0.1", port))
        self.socket.sendall(
            f"POST /v1/{route} HTTP/1.1\r\nHost: x\r\n"
            f"Authorization: Bearer {ADMIN}\r\nContent-Length: {len(data)}\r\n\r\n".encode()
            + data
        )
        self.read = 0

    def take(self, size: int) -> None:
        while size > 0:
            chunk = self.socket.recv(min(size, 4096))
            assert chunk, "the server closed the slow reader's answer"
            self.read += len(chunk)
            size -= len(chunk)

    def close(self) -> None:
        self.socket.close()


def timed(server: Server, route: str, body: dict[str, Any]) -> tuple[int, float]:
    started = time.monotonic()
    status, _ = raw(server, route, body)
    return status, time.monotonic() - started


def test_a_late_refusal_is_the_400_it_always_was(binary: Binary) -> None:
    plant_long(binary.home, 40, 900, bad=True)
    reason = "turn-log line 41 has invalid fields"
    envelope = {
        "error": {
            "code": "agent_conversation_format_unsupported",
            "message": f"Unsupported turn format in conversation '{LONG}': {reason}",
            "details": {"conversation_id": LONG, "reason": reason},
        }
    }
    wire = json.dumps(envelope, separators=(",", ":")).encode()
    with serve(binary) as server:
        # The malformed row lies past 180 KB of answer: the spool finds it
        # before anything is sent, as 2.16.0 found it before its body.
        bodies: list[dict[str, Any]] = [
            {"conversation_id": LONG},
            {"conversation_id": LONG, "limit": 1},
        ]
        for body in bodies:
            status, headers, answer = exchange(server, "conversation/read_turns", body)
            assert (status, answer) == (400, wire)
            assert headers["content-length"] == str(len(wire))
        remote = run(binary, "continue", LONG, "--url", server.url, env=REMOTE)
    local = run(binary, "continue", LONG)
    assert remote.returncode == local.returncode == 1
    assert remote.stdout == local.stdout == ""
    assert remote.stderr == local.stderr
    assert remote.stderr == (
        f"error: [agent_conversation_format_unsupported] {envelope['error']['message']}\n"
    )


def test_a_slow_reader_holds_nothing_of_the_store(binary: Binary) -> None:
    # A 20 MB answer read at a few KB per step by one client must not delay
    # another client's reads and writes on unrelated data (review F1).
    plant_long(binary.home, 300, 13_000)
    other = binary.home / "conversations" / "other-session"
    other.mkdir(parents=True)
    with serve(binary) as server:
        reader = SlowReader(
            server, "conversation/read_turns", {"conversation_id": LONG}
        )
        try:
            reader.take(65_536)
            probes: list[tuple[str, dict[str, Any]]] = [
                ("conversation/last_turn_number", {"conversation_id": LONG}),
                (
                    "conversation/append_turn",
                    {"conversation_id": "other-session", "messages": [user("hi")]},
                ),
                (
                    "memory/write",
                    {"scope": "user:other", "path": "notes/a", "content": "x"},
                ),
                ("conversation/read_turns", {"conversation_id": LONG, "limit": 1}),
            ]
            for route, body in probes:
                status, seconds = timed(server, route, body)
                assert status == 200 and seconds < 2.0, (route, status, seconds)
            reader.take(65_536)
            assert reader.read < 1_000_000, "the reader is still far from the end"
        finally:
            reader.close()


def test_file_searches_keep_one_log_open_at_a_time(binary: Binary) -> None:
    # 600 conversations, each with one matching turn; a limit-500 search
    # under an open-file limit of 256, launchd's default (review F4).
    for number in range(600):
        folder = binary.home / "conversations" / f"fd-{number:04d}"
        folder.mkdir(parents=True)
        row = {
            "neosian_format": 1,
            "turn": 1,
            "messages": [user(f"dashboard note {number}")],
            "created_at": f"2026-11-07T{number // 3600 % 24:02d}:{number // 60 % 60:02d}:{number % 60:02d}Z",
            "actor": "cli:fixture",
            "redacted": False,
        }
        (folder / "turns.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    limited = (
        "import os, resource, sys; "
        "resource.setrlimit(resource.RLIMIT_NOFILE, (256, resource.getrlimit(resource.RLIMIT_NOFILE)[1])); "
        "os.execv(sys.argv[1], sys.argv[1:])"
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = binary.environment()
    env["NEOSIAN_SERVE_TOKEN"] = f"client:contract={ADMIN}"
    command = [
        str(binary.executable),
        "serve",
        "--port",
        str(port),
        "--root",
        str(binary.home),
    ]
    process = subprocess.Popen(
        [sys.executable, "-c", limited, *command],
        env=env,
        cwd=binary.directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server = Server(f"http://127.0.0.1:{port}")
    try:
        for _ in range(400):
            try:
                if (
                    server.request("/health", method="GET", authenticated=False).status
                    == 200
                ):
                    break
            except OSError:
                time.sleep(0.05)
        for order in ["relevance", "newest"]:
            body = {"query": "dashboard", "limit": 500, "order": order}
            status, answer = raw(server, "conversation/search_turns", body)
            assert status == 200, (order, answer[:200])
            assert len(json.loads(answer)["turns"]) == 500
    finally:
        process.terminate()
        process.wait(timeout=10)
    local = subprocess.run(
        [
            sys.executable,
            "-c",
            limited,
            str(binary.executable),
            "search",
            "dashboard",
            "--limit",
            "500",
            "--json",
        ],
        env=binary.environment(),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert local.returncode == 0, local.stderr
    assert len(json.loads(local.stdout)["hits"]) == 500


def remote_and_local(binary: Binary, server: Server, *args: str) -> None:
    remote = run(binary, *args, "--url", server.url, env=REMOTE)
    local = run(binary, *args)
    assert remote.returncode == local.returncode == 0, remote.stderr
    if "--json" in args:
        mine, theirs = json.loads(remote.stdout), json.loads(local.stdout)
        assert mine.pop("client") == "client:contract"
        theirs.pop("client")
        assert mine == theirs
    else:
        assert remote.stdout == local.stdout


def test_remote_doors_answer_as_a_local_read(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    plant_long(binary.home, 30, 300)
    # A server that pages (with a cap below one turn) still gets 2.16.0's
    # requests from the binary, which answer as a local read.
    with serve(binary, extra_env={CAP: "64"}) as server:
        for args in [
            ("dashboard", "word", "--limit", "500"),
            ("dashboard", "--order", "newest", "--limit", "500"),
            ("dashboard", "--since", "2026-11-03T00:00:00Z", "--limit", "40"),
            ("response", "time"),
        ]:
            remote_and_local(binary, server, "search", *args, "--json")
            remote_and_local(binary, server, "search", *args)
        remote_and_local(binary, server, "continue", LONG)
        calls: list[dict[str, Any]] = [
            {"query": "dashboard word", "limit": 50},
            {"query": "dashboard", "order": "newest", "limit": 50},
            {"query": "long turn 7"},
        ]
        flags = ("--url", server.url, "--scope", "user:contract")
        _, mine = mcp(binary, calls, *flags, env=REMOTE)
    _, theirs = mcp(binary, calls)
    assert mine == theirs
    assert all(result.get("isError") is not True for result in mine)


@pytest.mark.parametrize("value", ["0", "ten", "1073741825"])
def test_the_page_cap_is_an_operator_setting(binary: Binary, value: str) -> None:
    plant_long(binary.home, 12, 300)
    body = {"conversation_id": LONG}
    with serve(binary, extra_env={CAP: value}) as server:
        refused = server.request(
            "/v1/conversation/read_turns", {**body, "page_bytes": 10}
        )
        assert (refused.status, refused.body) == (
            400,
            {
                "error": {
                    "code": "value_error",
                    "message": f"{CAP} must be an integer in 1..1073741824",
                    "details": {},
                }
            },
        )
        # Only a size-paged request reads the setting.
        assert server.request("/v1/conversation/read_turns", body).status == 200


def test_a_ceiling_above_the_cap_is_served_at_the_cap(binary: Binary) -> None:
    plant_long(binary.home, 12, 300)
    with serve(binary, extra_env={CAP: "4000"}) as server:
        whole = server.post("conversation/read_turns", conversation_id=LONG)["turns"]
        turns: list[Any] = []
        continuation = None
        pages = 0
        while True:
            body: dict[str, Any] = {"conversation_id": LONG, "page_bytes": 10**9}
            if continuation:
                body["continuation"] = continuation
            page = server.post("conversation/read_turns", **body)
            sizes = [
                len(json.dumps(t, separators=(",", ":")).encode())
                for t in page["turns"]
            ]
            assert sum(sizes) <= 4000
            turns += page["turns"]
            pages += 1
            continuation = page["continuation"]
            if continuation is None:
                break
        assert turns == whole and pages > 2


def test_spooled_answers_are_compact_whole_and_sized(binary: Binary) -> None:
    plant_long(binary.home, 120, 2200)
    with serve(binary) as server:
        requests: list[tuple[str, dict[str, Any]]] = [
            ("conversation/read_turns", {"conversation_id": LONG}),
            ("conversation/search_turns", {"query": "long dashboard", "limit": 500}),
            (
                "conversation/search_turns",
                {"query": "long word", "limit": 500, "order": "relevance"},
            ),
            ("memory/read", {"scope": "user:none", "path": "notes/absent"}),
        ]
        for route, body in requests:
            status, headers, text = exchange(server, route, body)
            assert status == 200 and b"\n" not in text
            # Every answer has its length again, small ones included (F7).
            assert headers["content-length"] == str(len(text))
            assert "transfer-encoding" not in headers
            parsed = json.loads(text)
            compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
            assert text == compact.encode()
            if "turns" in parsed:
                assert len(text) > 1 << 20 and len(parsed["turns"]) == 120
