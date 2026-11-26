"""A records-enabled store keeps every legacy route's bytes (DESIGN §40.54
item 15.1).

Two servers are sent the same bodies: one on a plain FileStore, one on a
FileStore that keeps records and holds only whole turns. Every answer is
compared as the bytes sent, status included. A write's answer carries the
time the server stamped and a change's id carries the stream the store drew,
and a page continuation binds the stamped turns it was cut from, so those
values are replaced by a fixed word on both sides before the comparison;
nothing else is replaced. The handshake differs by the one capability key,
last in the bytes. A turn written through `write_records` then shows the
two answers a legacy client meets on such a store: the refusal of a turn
too long to answer whole, and `omitted` on a search.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.records.support import enable, record, write
from tests.contract.schema import OPENAPI
from tests.contract.support import Binary, Server, executable, fixture, serve

SCOPE = "user:contract"
SESSION = "contract-session"
OTHER = "appended-session"
RECORD_ROUTES = {
    "/v1/conversation/write_records",
    "/v1/conversation/read_records",
    "/v1/conversation/search_records",
    "/v1/store/restore_records",
    "/v1/store/sweep_records",
}
TIME = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?Z")
STREAM = re.compile(rb"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
CONTINUATION = re.compile(rb'("continuation":")[A-Za-z0-9_=-]+')
CURSOR = re.compile(rb'("(?:cursor|head_cursor|earliest_cursor)":")([A-Za-z0-9_=-]+)"')
KEY = b',"turn_records":{"version":1}}'
LIMIT = {"NEOSIAN_MAX_TURN_BYTES": "65536"}
LONG = "signal " + "x" * 69_993


def sent(server: Server, path: str, body: Any = None) -> tuple[int, bytes]:
    """The status and the answer's bytes as sent; a GET when no body."""
    request = Request(
        server.url + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {server.token}"},
        method="GET" if body is None else "POST",
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def text(content: str) -> dict[str, Any]:
    return {"role": "user", "content": content}


def calls() -> list[tuple[str, Any, bool]]:
    """Every legacy route with the same bodies, in order: the path, the
    body, and whether the answer carries a value the server drew itself (a
    time it stamped, or a change stream's name). A body given as a function
    takes the `cursor` of that server's last `memory/changes` answer."""
    data: dict[str, Any] = fixture("records.json")
    memory = {"scope": SCOPE}
    fact = {**memory, "path": "notes/fact"}
    turns = {"conversation_id": SESSION}
    return [
        ("/health", None, False),
        (
            "/v1/store/restore_scope",
            {
                **memory,
                "documents": [data["document"]],
                "versions": data["versions"],
                "redactions": [data["redaction"]],
            },
            False,
        ),
        (
            "/v1/store/restore_conversation",
            {
                **turns,
                "turns": data["turns"],
                "projections": data["projections"],
                "redactions": [data["turn_redaction"]],
            },
            False,
        ),
        # What was restored carries its own times: every byte is compared.
        ("/v1/store/scopes", {}, False),
        ("/v1/store/conversations", {}, False),
        ("/v1/memory/read", fact, False),
        ("/v1/memory/read", {**memory, "path": "missing"}, False),
        ("/v1/memory/list_documents", memory, False),
        ("/v1/memory/list_documents", {**memory, "limit": 0}, False),
        ("/v1/memory/versions", fact, False),
        ("/v1/memory/history", memory, False),
        ("/v1/memory/redactions", memory, False),
        ("/v1/memory/search", {**memory, "query": "café"}, False),
        (
            "/v1/memory/search",
            {**memory, "query": "café", "order": "relevance", "passages": True},
            False,
        ),
        ("/v1/conversation/read_turns", turns, False),
        ("/v1/conversation/read_turns", {**turns, "after": 1, "limit": 1}, False),
        ("/v1/conversation/read_turns", {**turns, "page_bytes": 1}, False),
        ("/v1/conversation/last_turn_number", turns, False),
        ("/v1/conversation/last_turn_number", {"conversation_id": "absent"}, False),
        ("/v1/conversation/search_turns", {"query": "CAFÉ memory"}, False),
        (
            "/v1/conversation/search_turns",
            {"query": "café", "conversations": [SESSION], "order": "relevance"},
            False,
        ),
        (
            "/v1/conversation/search_turns",
            {"query": "café", "order": "newest", "page_bytes": 1},
            False,
        ),
        ("/v1/conversation/read_projections", turns, False),
        ("/v1/conversation/read_projections", {**turns, "after": 1}, False),
        ("/v1/conversation/turn_redactions", {"conversations": [SESSION]}, False),
        ("/v1/conversation/turn_redactions", {}, False),
        # Refusals, in the envelope and as plain text.
        ("/v1/memory/read", {"scope": "nonsense", "path": "x"}, False),
        ("/v1/memory/write", {**fact, "content": "x", "expected_version": 9}, False),
        ("/v1/conversation/read_turns", {"conversation_id": "has space"}, False),
        ("/v1/conversation/search_turns", {"query": ""}, False),
        ("/v1/conversation/append_turn", [], False),
        ("/v1/store/restore_conversation", {**turns, "turns": []}, False),
        ("/v1/missing", {}, False),
        ("/v1/memory/read", None, False),
        # Writes: the server stamps the time, and a change names its stream.
        ("/v1/memory/write", {**fact, "content": "third", "expected_version": 2}, True),
        ("/v1/memory/write", {**memory, "path": "notes/new", "content": "n"}, True),
        (
            "/v1/memory/rename",
            {**memory, "src": "notes/new", "dst": "notes/moved"},
            True,
        ),
        ("/v1/memory/delete", {**memory, "path": "notes/moved"}, True),
        ("/v1/memory/delete", {**memory, "path": "notes/moved"}, True),
        ("/v1/memory/redact", {**memory, "path": "notes/moved"}, True),
        ("/v1/memory/versions", fact, True),
        ("/v1/memory/history", memory, True),
        ("/v1/memory/redactions", memory, True),
        ("/v1/memory/list_documents", memory, True),
        ("/v1/memory/changes", memory, True),
        ("/v1/memory/changes", {**memory, "limit": 2}, True),
        # A cursor is the server's own: each side sends the one it was given.
        ("/v1/memory/prune_changes", lambda last: {**memory, "through": last}, True),
        (
            "/v1/memory/prune_changes",
            lambda last: {**memory, "through": last, "write": True},
            True,
        ),
        ("/v1/memory/changes", memory, True),
        (
            "/v1/conversation/append_turn",
            {"conversation_id": OTHER, "messages": [text("first one")]},
            True,
        ),
        (
            "/v1/conversation/append_turn",
            {
                "conversation_id": OTHER,
                "messages": [text("second one")],
                "actor": "agent",
            },
            True,
        ),
        (
            "/v1/conversation/append_projections",
            {
                "conversation_id": OTHER,
                "entries": [{"turn": 1, "kind": "log", "text": "noted", "span": 1}],
            },
            True,
        ),
        ("/v1/conversation/read_turns", {"conversation_id": OTHER}, True),
        ("/v1/conversation/read_projections", {"conversation_id": OTHER}, True),
        ("/v1/conversation/search_turns", {"query": "one"}, True),
        (
            "/v1/conversation/search_turns",
            {"query": "one", "order": "relevance", "page_bytes": 1},
            True,
        ),
        (
            "/v1/conversation/redact_turns",
            {"conversation_id": OTHER, "turns": [1], "actor": "eraser"},
            True,
        ),
        ("/v1/conversation/turn_redactions", {"conversations": [OTHER]}, True),
        ("/v1/conversation/read_turns", {"conversation_id": OTHER}, True),
        ("/v1/conversation/last_turn_number", {"conversation_id": OTHER}, True),
        ("/v1/store/scopes", {}, False),
        ("/v1/store/conversations", {}, False),
    ]


def plain_cursor(found: re.Match[bytes]) -> bytes:
    """A change cursor as the compact JSON it encodes."""
    return found.group(1) + base64.urlsafe_b64decode(found.group(2)) + b'"'


def masked(answer: bytes) -> bytes:
    """An answer with the values a server draws for itself replaced: a
    time it stamped, the name of a change stream, also where a cursor
    encodes it, and a page continuation, which binds the stamped turns it
    was cut from."""
    opened = CURSOR.sub(plain_cursor, TIME.sub(b"TIME", answer))
    return STREAM.sub(b"STREAM", CONTINUATION.sub(rb"\1BOUND", opened))


def compared(without: Server, with_records: Server) -> tuple[int, bytes]:
    """Send the script to a server on a plain store and to one on a store
    that keeps records, and compare every answer as bytes. The handshake of
    the store that keeps records is returned."""
    script = calls()
    # No legacy route of the published contract is left out.
    legacy = set(OPENAPI["paths"]) - RECORD_ROUTES - {"/v1/capabilities"}
    assert {path for path, _, _ in script} >= legacy
    ours = sent(without, "/v1/capabilities")
    theirs = sent(with_records, "/v1/capabilities")
    assert ours[0] == theirs[0] == 200
    # The one difference of the handshake: the key, last in the bytes.
    assert KEY not in ours[1] and b"turn_records" not in ours[1]
    assert theirs[1] == ours[1][:-1] + KEY
    statuses = []
    cursors = ["", ""]
    for path, body, drawn in script:
        bodies = [body(cursor) if callable(body) else body for cursor in cursors]
        before = sent(without, path, bodies[0])
        after = sent(with_records, path, bodies[1])
        statuses.append(before[0])
        if path == "/v1/memory/changes":
            cursors = [json.loads(side[1])["cursor"] for side in (before, after)]
        if drawn:
            assert before[0] == after[0], (path, body)
            assert masked(before[1]) == masked(after[1]), (path, body)
        else:
            assert before == after, (path, body)
    # The script met successes, refusals and the two plain-text answers.
    assert {200, 400, 404, 405} <= set(statuses)
    assert statuses.count(200) >= 50
    assert sent(with_records, "/v1/capabilities") == theirs
    return theirs


def test_every_legacy_route_answers_the_bytes_of_a_plain_store(
    tmp_path: Path,
) -> None:
    plain = Binary(executable(), tmp_path / "plain")
    kept = Binary(executable(), tmp_path / "kept")
    for binary in (plain, kept):
        binary.directory.mkdir()
    enable(kept)
    with serve(plain) as without, serve(kept) as with_records:
        compared(without, with_records)
        # The store that keeps records still holds whole turns only.
        assert not list((kept.home / "conversations").rglob("records"))


def test_a_long_turn_written_in_parts_is_refused_whole_and_omitted_from_search(
    tmp_path: Path,
) -> None:
    """A turn of 70,000 bytes written through `write_records`, read by the
    legacy routes under `NEOSIAN_MAX_TURN_BYTES=65536`: `read_turns` refuses
    it by name and `search_turns` leaves it out and counts it."""
    kept = Binary(executable(), tmp_path)
    enable(kept)
    refusal = {
        "error": {
            "code": "value_error",
            "message": "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 2 of"
            " long-session holds 70000 > 65536 bytes; a newer neosian reads this"
            " turn in parts",
            "details": {},
        }
    }
    with serve(kept, extra_env=LIMIT) as server:
        for content in ("signal square", None, "signal triangle"):
            if content is not None:
                server.post(
                    "conversation/append_turn",
                    conversation_id="long-session",
                    messages=[text(content)],
                )
                continue
            halves = [LONG[:35_000], LONG[35_000:]]
            items = [
                record(
                    index,
                    0,
                    "user",
                    "prompt",
                    half,
                    content="text" if index == 0 else None,
                    chunk=index,
                    size=70_000,
                )
                for index, half in enumerate(halves)
            ]
            write(server, "long-session", "d1", items[:1], turn_bytes=70_000)
            sealed = write(server, "long-session", "d1", items[1:], seal=True)
            assert (sealed["turn"]["turn"], sealed["turn"]["bytes"]) == (2, 70_000)
        body = {"conversation_id": "long-session"}
        status, answer = sent(server, "/v1/conversation/read_turns", body)
        assert (status, json.loads(answer)) == (400, refusal)
        paged = {**body, "page_bytes": 983_040}
        status, answer = sent(server, "/v1/conversation/read_turns", paged)
        assert (status, json.loads(answer)) == (400, refusal)
        # The turns beside it still read, and the count is unchanged.
        one = server.post("conversation/read_turns", **body, limit=1)
        assert [turn["turn"] for turn in one["turns"]] == [1]
        last = server.post("conversation/last_turn_number", **body)
        assert last == {"turn": 3}
        for more, keys in [
            ({}, ["turns", "omitted"]),
            ({"order": "newest"}, ["turns", "order", "omitted"]),
            ({"order": "relevance"}, ["turns", "order", "scores", "omitted"]),
        ]:
            status, answer = sent(
                server, "/v1/conversation/search_turns", {"query": "signal", **more}
            )
            found = json.loads(answer)
            assert status == 200 and list(found) == keys, more
            assert found["omitted"] == 1
            assert sorted(turn["turn"] for turn in found["turns"]) == [1, 3]
            assert answer.endswith(b',"omitted":1}')
        # A word only the long turn holds: an empty answer that counts it.
        status, answer = sent(
            server, "/v1/conversation/search_turns", {"query": "xxxx"}
        )
        assert (status, answer) == (200, b'{"turns":[],"omitted":1}')
        # A search that does not reach the long turn has no `omitted` key.
        status, answer = sent(
            server, "/v1/conversation/search_turns", {"query": "square"}
        )
        assert status == 200 and list(json.loads(answer)) == ["turns"]
    # Without the lowered ceiling the same turn is answered whole.
    with serve(kept) as server:
        whole = server.post("conversation/read_turns", conversation_id="long-session")
        assert [turn["messages"][0]["content"] for turn in whole["turns"]] == [
            "signal square",
            LONG,
            "signal triangle",
        ]
