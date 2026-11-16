"""Streamed answers and size paging through the binary's doors (S14, §40.46).

Large answers stream with unchanged bytes. A refusal found after the first
64 KiB have left arrives as a trailer: a line feed and the error envelope, so
the 200 body is no longer JSON, and the binary's remote client reports the
error a local read reports. Against a server that advertises size paging, the
remote client pages and its doors answer exactly as a local read does, even
when the server's cap holds one turn a page. Expected texts are literal.
"""

from __future__ import annotations

import json
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
    request = Request(
        f"{server.url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {ADMIN}"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_a_refusal_after_the_first_bytes_is_a_trailer(binary: Binary) -> None:
    plant_long(binary.home, 40, 900, bad=True)
    reason = "turn-log line 41 has invalid fields"
    envelope = {
        "error": {
            "code": "agent_conversation_format_unsupported",
            "message": f"Unsupported turn format in conversation '{LONG}': {reason}",
            "details": {"conversation_id": LONG, "reason": reason},
        }
    }
    with serve(binary) as server:
        status, body = raw(server, "conversation/read_turns", {"conversation_id": LONG})
        assert status == 200 and len(body) > 64 * 1024
        with pytest.raises(json.JSONDecodeError):
            json.loads(body)
        answer, trailer = body.split(b"\n", 1)
        assert answer.startswith(b'{"turns":[{"conversation_id":"long-session"')
        assert json.loads(trailer) == envelope
        assert trailer == json.dumps(envelope, separators=(",", ":")).encode()
        # Refused before 64 KiB left, the same refusal is an ordinary 400.
        short = {"conversation_id": LONG, "limit": 1}
        assert raw(server, "conversation/read_turns", short) == (
            400,
            json.dumps(envelope, separators=(",", ":")).encode(),
        )
        remote = run(binary, "continue", LONG, "--url", server.url, env=REMOTE)
    local = run(binary, "continue", LONG)
    assert remote.returncode == local.returncode == 1
    assert remote.stdout == local.stdout == ""
    assert remote.stderr == local.stderr
    assert remote.stderr == (
        f"error: [agent_conversation_format_unsupported] {envelope['error']['message']}\n"
    )


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


def test_remote_doors_page_and_answer_as_a_local_read(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    plant_long(binary.home, 30, 300)
    # A cap below one turn: every page holds exactly one turn.
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


def test_streamed_answers_are_compact_and_whole(binary: Binary) -> None:
    plant_long(binary.home, 120, 900)
    with serve(binary) as server:
        requests: list[tuple[str, dict[str, Any]]] = [
            ("conversation/read_turns", {"conversation_id": LONG}),
            ("conversation/search_turns", {"query": "long dashboard", "limit": 500}),
            (
                "conversation/search_turns",
                {"query": "long word", "limit": 500, "order": "relevance"},
            ),
        ]
        for route, body in requests:
            status, text = raw(server, route, body)
            assert status == 200 and len(text) > 64 * 1024 and b"\n" not in text
            parsed = json.loads(text)
            assert len(parsed["turns"]) == 120
            compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
            assert text == compact.encode()
