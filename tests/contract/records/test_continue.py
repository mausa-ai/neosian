"""`continue_session` and `neosian continue` deliver the released light
handoff and offer explicit views (DESIGN §40.54 item 19, #448), alike on a
server that keeps records and on one that does not.

Two turns are written as records to a records-enabled FileStore behind a
server and whole to a plain one, with a pending note on each. The CLI and
stdio MCP ask both for every view behind a proxy that keeps each request:
each answer matches a literal composed here with that server's own turn
times, so the two differ in nothing else; the records server
is read with `read_records` only, its headers first and then through the
last turn they named, and the plain one with `read_turns` only. The CLI
leaves the note pending and keeps its released JSON envelope; MCP picks the
note up with the first delivery. A failed read fails both doors and leaves
the note pending. Four turns holding 4 MiB results are continued from
heads alone.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.contract.records.continuing import (
    CONVERSATION,
    SCOPE,
    ends,
    expected,
    note,
    pending,
    records_of,
    store,
    tools,
)
from tests.contract.records.recalling import capture, run, text
from tests.contract.records.support import enable, write
from tests.contract.records.test_recall import hosted
from tests.contract.support import Binary, serve

VIEWS = ("light", "prompts", "calls", "full")


def cli(binary: Binary, url: str, token: str, *flags: str) -> Any:
    env = {"NEOSIAN_CLIENT_TOKEN": token}
    args = ("continue", CONVERSATION, "--url", url, "--scope", SCOPE, *flags)
    return run(binary, *args, env=env)


def history(exchanges: list[tuple[str, str, bytes, int]]) -> list[tuple[str, Any]]:
    """The conversation routes asked, with their bodies."""
    return [
        (path.rsplit("/", 1)[1], json.loads(body))
        for _, path, body, _ in exchanges
        if path.startswith("/v1/conversation/")
    ]


def test_every_view_reads_alike_with_and_without_records(
    binary: Binary, tmp_path: Path
) -> None:
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    with serve(binary) as kept, serve(plain) as legacy:
        store(kept, legacy)
        facts = {"kept": ends(kept, True), "legacy": ends(legacy, False)}
        answers: dict[str, Any] = {}
        for name, server in (("kept", kept), ("legacy", legacy)):
            note(server)
            with capture(server) as held:
                printed = [
                    cli(binary, held.url, server.token, "--view", view)
                    for view in VIEWS
                ]
                envelope = cli(binary, held.url, server.token, "--json")
                assert pending(server)
                asked = [{"conversation": CONVERSATION}]
                asked += [{"conversation": CONVERSATION, "view": v} for v in VIEWS]
                delivered = tools(binary, held.url, server.token, asked)
            assert not pending(server)
            routes = history(held.exchanges)
            answers[name] = ([p.stdout for p in printed], envelope.stdout, delivered)
            for process in [*printed, envelope]:
                assert (process.returncode, process.stderr) == (0, "")
            names = {route for route, _ in routes}
            if name == "kept":
                assert names == {"read_records", "read_projections"}, names
                reads = [body for route, body in routes if route == "read_records"]
                assert all(body["conversation_id"] == CONVERSATION for body in reads)
                assert len(reads) == 2 * 10, reads
                for header, viewed in zip(reads[::2], reads[1::2], strict=True):
                    assert header == {"conversation_id": CONVERSATION, "kinds": []}
                    assert viewed["through"] == 2 and "after" not in viewed
                    assert not {"reasoning", "extra"} & set(viewed["kinds"])
                    assert viewed["heads"]["attachment"] == 1
                    assert viewed["heads"]["prompt"] == 32_768
            else:
                assert names == {"read_turns", "read_projections"}, names
        # Each server's answers are the literals with its own turn times.
        for name, (printed, envelope, delivered) in answers.items():
            at = facts[name]
            assert printed == [expected(view, at, True) + "\n" for view in VIEWS]
            decoded = json.loads(envelope)
            assert list(decoded) == ["conversation", "scope", "note", "client", "text"]
            assert decoded["text"] == expected("light", at, True)
            assert (decoded["conversation"], decoded["scope"], decoded["note"]) == (
                CONVERSATION,
                SCOPE,
                True,
            )
            shown = [text(result) for result in delivered]
            assert shown[0] == expected("light", at, True)
            assert shown[1:] == [expected(view, at, False) for view in VIEWS]
            assert not any(result.get("isError") for result in delivered)
        assert facts["kept"][0] == facts["legacy"][0]
        calls = {"conversation": CONVERSATION, "view": "calls"}
        assert hosted(kept, calls, "continue_session") == expected(
            "calls", facts["kept"], False
        )


def test_a_failed_read_fails_both_doors_and_leaves_the_note(
    binary: Binary, tmp_path: Path
) -> None:
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    with serve(binary) as kept, serve(plain) as legacy:
        store(kept, legacy)
        for server, route in ((kept, "read_records"), (legacy, "read_turns")):
            note(server)
            with capture(server, fail=route) as held:
                printed = cli(binary, held.url, server.token)
                [delivered] = tools(
                    binary, held.url, server.token, [{"conversation": CONVERSATION}]
                )
            assert (printed.returncode, printed.stdout) == (1, ""), printed
            assert printed.stderr.startswith("error: ") and "injected" in printed.stderr
            assert delivered["isError"] is True
            assert "injected read failure" in text(delivered)
            assert pending(server), route


def test_large_results_are_continued_from_heads(binary: Binary) -> None:
    result = "HEAD " + "z " * (2 << 20) + "MIDDLE " + "y " * (2 << 20) + "TAIL"
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "show the log"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "r", "name": "Bash", "arguments": {"cmd": "cat"}}],
        },
        {"role": "tool", "content": result, "tool_call_id": "r"},
        {"role": "assistant", "content": "It is long."},
    ]
    items = records_of(messages)
    piece = next(item for item in items if item["kind"] == "tool_result")
    chunks = [result[at : at + 60_000] for at in range(0, len(result), 60_000)]
    at = items.index(piece)
    split = [
        {
            **piece,
            "seq": at + n,
            "chunk": n,
            "text": chunk,
            "content": None if n else piece["content"],
        }
        for n, chunk in enumerate(chunks)
    ]
    tail = [
        {**item, "seq": at + len(chunks) + n} for n, item in enumerate(items[at + 1 :])
    ]
    records = items[:at] + split + tail
    enable(binary)
    with serve(binary) as kept:
        for number in range(1, 5):
            for start in range(0, len(records), 32):
                part = records[start : start + 32]
                seal = start + 32 >= len(records)
                write(kept, "continue-big", f"d-{number}", part, seal=seal)
        with capture(kept) as held:
            env = {"NEOSIAN_CLIENT_TOKEN": kept.token}
            args = ("continue", "continue-big", "--url", held.url, "--scope", SCOPE)
            printed = run(binary, *args, env=env)
        assert printed.returncode == 0, printed.stderr
        assert "HEAD" in printed.stdout and "TAIL" in printed.stdout
        assert "MIDDLE" not in printed.stdout
        reads = [
            size for _, path, _, size in held.exchanges if path.endswith("read_records")
        ]
        assert len(reads) == 2, held.exchanges
        assert sum(reads) < 4 * 2 * 8_192 + 16_384, reads
