"""`recall_turn` and `neosian recall` show a light turn and open one part
(DESIGN §40.54 item 19, #448), alike on a server that keeps records and on
one that does not.

One turn is written as records to a records-enabled FileStore behind a
server and whole to a plain one. Every view and part is asked of both
through stdio MCP over `--url`, behind a proxy that keeps each request: the
answers are equal and match literals computed here, the records server is
read with `read_records` only and the plain one with `read_turns` only. The
CLI and the hosted `/mcp` answer the same text. A 16 MiB result stays out
of the light view and of every part but the one asked, and the bytes each
answer took are bounded.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

from tests.contract.records.recalling import (
    CALL,
    Turn,
    capture,
    head_of,
    mcp,
    result_text,
    run,
    split,
    store,
    tail_of,
    text,
)
from tests.contract.records.support import enable
from tests.contract.support import Binary, Server, serve

SCOPE = {"NEOSIAN_SCOPE": "user:contract"}
PARITY = "recall-parity"
BIG = "recall-big"


def flat(value: str) -> str:
    return " ".join(value.split())


def grouped(number: int) -> str:
    return f"{number:,}"


def omitted(whole: str, head: str, tail: str) -> str:
    return grouped(len(whole.encode()) - len(head.encode()) - len(tail.encode()))


def parity() -> Turn:
    arguments = {"path": "/w", "text": "x " * 60_000}
    return Turn('Review café "memory"\nnow', arguments, result_text(12_000))


def expected_views(turn: Turn) -> dict[str, str]:
    size = grouped(len(turn.result.encode()))
    opens = f'pass call="{CALL}" to read it'
    args = flat(turn.arguments_text)[:80].rstrip() + " …"
    front, back = head_of(turn.result, 2_048), tail_of(turn.result, 2_048)
    digest = flat(front)[:100].rstrip() + " … " + flat(back)[-40:].lstrip()
    user = 'USER: Review café "memory"\nnow'
    cut = omitted(turn.result, front, back)
    marker = f" … [{cut} of {size} bytes omitted; {opens} in parts] … "
    return {
        "light": "\n".join(
            [
                "Turn 1 (light: prompts and replies verbatim, one line per tool call):",
                user,
                f"TOOL Write({args}) → {digest} [{size} bytes; {opens}]",
                "AGENT: All done.",
            ]
        ),
        "prompts": f"Turn 1 (prompts):\n{user}",
        "calls": "\n".join(
            [
                "Turn 1 (calls: prompts, replies and every call whole, results by size):",
                user,
                f"AGENT calls Write({turn.arguments_text})",
                f"TOOL Write → [{size} bytes; {opens}]",
                "AGENT: All done.",
            ]
        ),
        "full": "\n".join(
            [
                "Turn 1 (verbatim):",
                user,
                f"AGENT calls Write({turn.arguments_text})",
                f"TOOL Write → {front}{marker}{back}",
                "AGENT: All done.",
            ]
        ),
    }


def expected_part(turn: Turn, part: int) -> str:
    chunks = split(turn.result)
    size = grouped(len(turn.result.encode()))
    lines = [f"Turn 1, call {CALL}{f', part {part}' if part else ''} (verbatim):"]
    if part == 0:
        whole = turn.arguments_text
        front, back = head_of(whole, 512), tail_of(whole, 512)
        cut = omitted(whole, front, back)
        args = f"{front} … [{cut} of {grouped(len(whole.encode()))} bytes omitted; pass seq=1 to read it in parts] … {back}"
        lines.append(f"AGENT calls Write({args})")
    lines.append(f"TOOL Write → {chunks[part]}")
    if part + 1 < len(chunks):
        lines.append(
            f"… [part {part} of a result of {size} bytes; pass part={part + 1} to continue]"
        )
    elif part:
        lines.append(f"… [part {part}, the last, of a result of {size} bytes]")
    return "\n".join(lines)


def asked(turn: Turn) -> list[dict[str, Any]]:
    middle = turn.first("tool_result") + 3
    count = len(split(turn.result))
    calls: list[dict[str, Any]] = [{"turn": 1, "conversation": PARITY}]
    calls += [
        {"turn": 1, "conversation": PARITY, "view": v}
        for v in ("prompts", "calls", "full")
    ]
    calls += [
        {"turn": 1, "conversation": PARITY, "call": CALL, "part": p}
        for p in range(count + 1)
    ]
    calls += [
        {"turn": 1, "conversation": PARITY, "seq": middle},
        {"turn": 1, "conversation": PARITY, "seq": middle, "part": 2},
        {"turn": 1, "conversation": PARITY, "call": "nope"},
        {"turn": 2, "conversation": PARITY},
        {"turn": 1, "conversation": "bad id"},
    ]
    return calls


def hosted(server: Server, arguments: dict[str, Any], tool: str = "recall_turn") -> str:
    opener = build_opener(ProxyHandler({}))
    headers = {
        "Authorization": f"Bearer {server.token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    def post(body: dict[str, Any], extra: dict[str, str]) -> tuple[Any, str]:
        sent = Request(
            server.url + "/mcp",
            json.dumps(body).encode(),
            headers | extra,
            method="POST",
        )
        with opener.open(sent, timeout=30) as response:
            return response.headers, response.read().decode()

    opened, _ = post(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {}
    )
    session = {"mcp-session-id": opened["mcp-session-id"]}
    call = {"name": tool, "arguments": arguments}
    _, answer = post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call}, session
    )
    data = json.loads(answer.split("data: ", 1)[1].splitlines()[0])
    return text(data["result"])


def test_views_and_parts_read_alike_with_and_without_records(
    binary: Binary, tmp_path: Path
) -> None:
    turn = parity()
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    calls = asked(turn)
    with serve(binary, extra_env=SCOPE) as kept, serve(plain) as legacy:
        store(kept, PARITY, turn.records())
        legacy.post(
            "conversation/append_turn", conversation_id=PARITY, messages=turn.messages()
        )
        answers = {}
        for name, server in (("kept", kept), ("legacy", legacy)):
            with capture(server) as held:
                env = {"NEOSIAN_CLIENT_TOKEN": server.token}
                answers[name] = mcp(binary, ["--url", held.url], calls, env)
            paths = {
                path
                for _, path, *_ in held.exchanges
                if path.startswith("/v1/conversation/")
            }
            reads = [
                json.loads(body)
                for _, path, body, _ in held.exchanges
                if path.endswith("read_turns")
            ]
            if name == "kept":
                assert paths == {"/v1/conversation/read_records"}, paths
            else:
                assert paths == {"/v1/conversation/read_turns"}, paths
                assert {json.dumps(body) for body in reads} == {
                    json.dumps({"conversation_id": c, "after": 0, "limit": None})
                    for c in (PARITY, "bad id")
                }
        assert answers["kept"] == answers["legacy"]
        shown = [text(result) for result in answers["kept"]]
        views = expected_views(turn)
        assert shown[:4] == [
            views["light"],
            views["prompts"],
            views["calls"],
            views["full"],
        ]
        count = len(split(turn.result))
        assert count >= 8
        for part in range(count):
            assert shown[4 + part] == expected_part(turn, part), part
        past = answers["kept"][4 + count]
        assert past["isError"] is True
        assert text(past).startswith(f"Part {count} is past the end of a result of ")
        start, chunks = turn.first("tool_result"), split(turn.result)
        size = grouped(len(turn.result.encode()))
        middle, second = shown[5 + count], shown[6 + count]
        assert middle == (
            f"Turn 1, seq {start} (verbatim):\nTOOL Write → {chunks[0]}\n"
            f"… [part 0 of a result of {size} bytes; pass part=1 to continue]"
        )
        assert second.startswith(
            f"Turn 1, seq {start}, part 2 (verbatim):\nTOOL Write → {chunks[2]}\n"
        )
        assert shown[7 + count] == f"Turn 1 made no call 'nope'\nIts calls: {CALL}."
        assert (
            shown[8 + count]
            == f"Turn 2 does not exist\nConversation '{PARITY}' has turns 1-1."
        )
        assert answers["kept"][9 + count]["isError"] is True
        env = {"NEOSIAN_CLIENT_TOKEN": kept.token}
        cli = run(binary, "recall", PARITY, "1", "--url", kept.url, env=env)
        assert (cli.returncode, cli.stdout, cli.stderr) == (
            0,
            views["light"] + "\n",
            "",
        )
        cli = run(
            binary,
            "recall",
            PARITY,
            "1",
            "--call",
            CALL,
            "--part",
            "1",
            "--url",
            legacy.url,
            env=env,
        )
        assert (cli.returncode, cli.stdout) == (0, expected_part(turn, 1) + "\n")
        assert hosted(kept, {"turn": 1, "conversation": PARITY}) == views["light"]
        assert hosted(
            kept, {"turn": 1, "conversation": PARITY, "call": CALL, "part": 3}
        ) == expected_part(turn, 3)
    local = run(
        binary, "recall", PARITY, "1", "--view", "full", "--root", str(binary.home)
    )
    assert (local.returncode, local.stdout) == (0, views["full"] + "\n")


def big() -> tuple[Turn, list[list[str]]]:
    base = "z" * 60_000
    chunks = [base] * 280
    chunks[0] = "HEAD-CANARY " + base[12:]
    chunks[140] = base[:30_000] + "MIDDLE-CANARY" + base[30_013:]
    chunks[-1] = base[:-11] + "TAIL-CANARY"
    turn = Turn(
        "Summarize the log", {"path": "/big", "text": "y " * 150_000}, "".join(chunks)
    )
    pieces = turn.pieces()
    pieces[2] = chunks
    return turn, pieces


def test_a_sixteen_mebibyte_result_stays_out_of_light_and_every_other_part(
    binary: Binary,
) -> None:
    turn, pieces = big()
    assert len(turn.result.encode()) >= 16 << 20
    records = turn.records(pieces)
    enable(binary)
    with serve(binary) as kept:
        store(kept, BIG, records)
        env = {"NEOSIAN_CLIENT_TOKEN": kept.token}
        with capture(kept) as held:
            [light] = mcp(
                binary, ["--url", held.url], [{"turn": 1, "conversation": BIG}], env
            )
        shown = text(light)
        assert (
            "HEAD-CANARY" in shown
            and "TAIL-CANARY" in shown
            and "MIDDLE-CANARY" not in shown
        )
        assert f'[16,800,000 bytes; pass call="{CALL}" to read it]' in shown
        assert len(shown.encode()) < 4_096
        assert held.answered() < 32_768, held.exchanges
        start = turn.first("tool_result", records)
        for selector in ({"call": CALL}, {"seq": start + 140}):
            for part in (0, 140, 279):
                with capture(kept) as held:
                    call = {"turn": 1, "conversation": BIG, "part": part, **selector}
                    [answer] = mcp(binary, ["--url", held.url], [call], env)
                lines = text(answer).split("\n")
                assert f"→ {pieces[2][part]}" in lines[-2], part
                assert held.answered() < 3 * 81_920 + 32_768, (part, held.answered())
            assert (
                lines[-1] == "… [part 279, the last, of a result of 16,800,000 bytes]"
            )
    local = run(binary, "recall", BIG, "1", "--root", str(binary.home))
    assert (local.returncode, local.stdout) == (0, shown + "\n")


def test_the_cli_refuses_what_recall_cannot_mean(binary: Binary) -> None:
    for args, error in (
        (["0"], "Turn numbers start at 1; got 0"),
        (["1", "--call", "x", "--seq", "1"], "Pass call or seq, not both"),
        (["1", "--part", "1"], "part needs call or seq"),
        (["1", "--seq", "-1"], "seq must be at least 0; got -1"),
        (["1", "--call", "x", "--part", "-1"], "part must be at least 0; got -1"),
        (
            ["1", "--view", "full", "--call", "x"],
            "view shows a whole turn; call and seq open one part of it",
        ),
    ):
        result = run(binary, "recall", PARITY, *args)
        assert result.returncode == 2, args
        assert result.stdout == ""
        assert result.stderr.startswith(f"error: {error}\nhint: "), result.stderr
    assert run(binary, "recall", PARITY, "1", "--view", "nope").returncode == 2
    assert run(binary, "recall", "bad id", "1").returncode == 2
    with serve(binary) as plain:
        plain.post(
            "conversation/append_turn",
            conversation_id=PARITY,
            messages=parity().messages(),
        )
    past = run(binary, "recall", PARITY, "1", "--call", CALL, "--part", "99")
    assert past.returncode == 1
    assert past.stderr.startswith("error: Part 99 is past the end of a result of ")


def test_a_turn_records_cannot_carry_is_shown_whole_on_both_servers(
    binary: Binary, tmp_path: Path
) -> None:
    long = "c" * 1_025
    messages = [
        {"role": "user", "content": "look"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": long, "name": "Read", "arguments": {"path": "/x"}}],
        },
        {"role": "tool", "content": "ok", "tool_call_id": long},
        {"role": "assistant", "content": "done"},
    ]
    whole = (
        "[this turn cannot be shown in views; it is shown whole, as recorded]\n"
        'Turn 1 (verbatim):\nUSER: look\nAGENT calls Read({"path":"/x"})\n'
        "TOOL Read → ok\nAGENT: done"
    )
    call = f'Turn 1, call {long} (verbatim):\nAGENT calls Read({{"path":"/x"}})\nTOOL Read → ok'
    calls = [
        {"turn": 1, "conversation": PARITY},
        {"turn": 1, "conversation": PARITY, "view": "calls"},
        {"turn": 1, "conversation": PARITY, "call": long},
        {"turn": 2, "conversation": PARITY},
    ]
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    with serve(binary) as kept, serve(plain) as legacy:
        for server in (kept, legacy):
            server.post(
                "conversation/append_turn", conversation_id=PARITY, messages=messages
            )
            env = {"NEOSIAN_CLIENT_TOKEN": server.token}
            answers = mcp(binary, ["--url", server.url], calls, env)
            assert [text(answer) for answer in answers[:3]] == [whole, whole, call]
            assert [answer["isError"] for answer in answers] == [
                False,
                False,
                False,
                True,
            ]
            missing = f"Turn 2 does not exist\nConversation '{PARITY}' has turns 1-1."
            assert text(answers[3]) == missing
