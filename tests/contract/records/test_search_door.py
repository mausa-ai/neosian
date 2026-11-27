"""`search_history` and `neosian search` name a record per hit (DESIGN §40.54
items 11, 15.2 and 19, #448), on a store that keeps records and on one that
does not.

Every expected answer is written here from the records that were stored and
their turns' headers: a hit's header, its excerpt by memory search's rule
(`searching.excerpt`) and, under relevance, the literal scores of the
independent reference that `test_search_hits.py` pins. A proxy keeps every
request the binary sends, so a refusal is seen not to be asked again and a
profile is seen to send only what its history admits.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.contract.profile_support import (
    PREFIX,
    PROFILE as BASE,
    bind,
    grant_env,
    install,
)
from tests.contract.records.recalling import (
    CALL,
    Turn,
    capture,
    run,
    split,
    store,
    text,
)
from tests.contract.records.searching import excerpt, hit
from tests.contract.records.support import enable, record, write
from tests.contract.records.test_recall import hosted
from tests.contract.records.test_search_hits import (
    ALL_FIVE,
    PARTS,
    SESSION,
    TERMS,
    WHOLE,
    corpus,
)
from tests.contract.support import Binary, Server, serve

SCOPE = ("--scope", "user:contract")
NAMES = "prompt, reply, tool_call, tool_result and system"
RANKED = (
    "[best match first, at most {}; pass conversation, turn and seq to "
    'recall_turn to open a hit\'s piece, or view="full" to read its turn]'
)
NEWEST = (
    "[newest first, at most {}; pass conversation, turn and seq to "
    'recall_turn to open a hit\'s piece, or view="full" to read its turn]'
)
DERIVED = (
    "this store keeps no records, so these hits come from the turns a turn "
    "search found and are an approximation of a record search"
)


def kinds_note(names: str) -> str:
    return f"only {names} records are searched; mailbox messages are not"


def tools(
    binary: Binary,
    flags: list[str],
    calls: list[dict[str, Any]],
    env: dict[str, str] | None = None,
    tool: str = "search_history",
    scope: str = "user:contract",
) -> list[Any]:
    """Calls of one tool over stdio MCP; each answer's `result`."""
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "contract", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for number, arguments in enumerate(calls, 1):
        params = {"name": tool, "arguments": arguments}
        requests.append(
            {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": params}
        )
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    process = run(binary, "mcp", *flags, "--scope", scope, env=env, stdin=stdin)
    assert process.returncode == 0, process.stderr
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    return [answer["result"] for answer in answers[1:]]


def shown(result: dict[str, Any]) -> str:
    assert result.get("isError") is not True, result
    return text(result)


def refused(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    return text(result)


def cli(binary: Binary, *args: str, env: dict[str, str] | None = None) -> Any:
    result = run(binary, "search", *args, "--json", env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def head(found: dict[str, Any]) -> str:
    """Item 19's header of a hit, from its keys."""
    name = f" {found['name']}" if found["name"] else ""
    return (
        f"[{found['conversation_id']} #{found['turn']}.{found['seq']} "
        f"{found['kind']}{name}] {found['created_at']} {found['actor'] or '-'}"
    )


def line(found: dict[str, Any]) -> str:
    """The excerpt on one line, white space folded, `…` when cut in front."""
    words = " ".join(found["excerpt"]["text"].split())
    return f"… {words}" if found["excerpt"]["offset"] else words


def block(found: dict[str, Any]) -> str:
    return f"{head(found)}\n  {line(found)}"


def typed(found: dict[str, Any]) -> dict[str, Any]:
    return {"type": "record", **found}


def searched(held: Any) -> list[tuple[str, Any]]:
    """Each conversation route the binary asked, with its parsed body."""
    return [
        (path.removeprefix("/v1/conversation/"), json.loads(body) if body else None)
        for _, path, body, _ in held.exchanges
        if path.startswith("/v1/conversation/")
    ]


def expected_hits(
    first: dict[str, Any], second: dict[str, Any], rows: list[tuple[int, int, int]]
) -> list[dict[str, Any]]:
    headers, items = {1: first, 2: second}, {1: WHOLE, 2: PARTS}
    names = {(2, 4): "deploy_cache"}
    return [
        hit(
            SESSION,
            headers[turn],
            items[turn][seq],
            TERMS,
            name=names.get((turn, seq)),
            score=score,
        )
        for turn, seq, score in rows
    ]


def test_record_hits_in_text_and_json_on_both_doors(
    records: Server, binary: Binary
) -> None:
    first, second = corpus(records)
    ranked = expected_hits(first, second, ALL_FIVE)
    env = {"NEOSIAN_CLIENT_TOKEN": records.token}
    with capture(records) as held:
        answers = tools(
            binary,
            ["--url", held.url],
            [
                {"query": "deploy CACHE"},
                {
                    "query": "deploy cache",
                    "order": "newest",
                    "kinds": ["tool_result", "tool_call"],
                },
            ],
            env,
        )
    assert shown(answers[0]) == "\n".join(
        [*(block(found) for found in ranked), RANKED.format(10)]
    )
    # The hosted /mcp answers the same text from the server's own records.
    assert hosted(records, {"query": "deploy CACHE"}, "search_history") == shown(
        answers[0]
    )
    newest = [
        {k: v for k, v in found.items() if k != "score"}
        for found in expected_hits(first, second, [(2, 2, 0), (2, 4, 0)])
    ]
    assert shown(answers[1]) == "\n".join(
        [
            f"[{kinds_note('tool_result, tool_call')}]",
            *(block(found) for found in newest),
            NEWEST.format(10),
        ]
    )
    assert searched(held) == [
        (
            "search_records",
            {"query": "deploy CACHE", "order": "relevance", "limit": 10},
        ),
        (
            "search_records",
            {
                "query": "deploy cache",
                "kinds": ["tool_result", "tool_call"],
                "order": "newest",
                "limit": 10,
            },
        ),
    ]
    page = cli(binary, "deploy", "CACHE", "--url", records.url, env=env)
    assert page == {
        "query": "deploy CACHE",
        "conversations": None,
        "kinds": None,
        "limit": 20,
        "client": page["client"],
        "hits": [typed(found) for found in ranked],
        "order": "relevance",
    }
    assert [(h["turn"], h["seq"], h["score"]) for h in page["hits"]] == ALL_FIVE
    human = run(binary, "search", "deploy", "CACHE", "--url", records.url, env=env)
    assert (human.returncode, human.stderr) == (0, "")
    assert human.stdout == "".join(
        ["order: relevance\n", *(f"{head(f)}  {line(f)}\n" for f in ranked)]
    )
    replies = cli(
        binary, "deploy", "CACHE", "--kind", "reply", "--kind", "prompt",
        "--url", records.url, env=env,
    )  # fmt: skip
    assert replies["kinds"] == ["reply", "prompt"]
    assert [(h["turn"], h["seq"], h["score"]) for h in replies["hits"]] == [
        (1, 1, 658961),
        (1, 0, 640996),
        (2, 1, 164840),
    ]


def test_the_only_match_in_a_later_chunk_names_that_record_and_recall_opens_it(
    records: Server, binary: Binary
) -> None:
    filler = "alpha beta gamma delta\n" * 9_000
    turn = Turn(
        "Read the log", {"path": "/log"}, filler + "ZEPHYR-CANARY seen\n" + filler
    )
    chunks = split(turn.result)
    items = turn.records()
    start = turn.first("tool_result", items)
    later = next(n for n, chunk in enumerate(chunks) if "ZEPHYR-CANARY" in chunk)
    assert later >= 2
    store(records, "s-long", items)
    header = records.post("conversation/read_records", conversation_id="s-long")
    created = header["turns"][0]["created_at"]
    actor = header["turns"][0]["actor"]
    expected = {
        "conversation_id": "s-long",
        "turn": 1,
        "seq": start + later,
        "message": 2,
        "kind": "tool_result",
        "call_id": CALL,
        "name": "Write",
        "created_at": created,
        "actor": actor,
        "size": len(turn.result.encode()),
        "excerpt": excerpt(chunks[later], ["zephyr-canary"]),
    }
    env = {"NEOSIAN_CLIENT_TOKEN": records.token}
    page = cli(
        binary, "zephyr-canary", "--order", "newest", "--url", records.url, env=env
    )
    assert page["hits"] == [typed(expected)]
    [found] = tools(binary, ["--url", records.url], [{"query": "ZEPHYR-CANARY"}], env)
    assert shown(found) == f"{block(expected)}\n{RANKED.format(10)}"
    assert line(expected).startswith("… ")
    # The hit's coordinates open its piece from the beginning; its part is
    # the record's place in the piece.
    opened = tools(
        binary,
        ["--url", records.url],
        [
            {"conversation": "s-long", "turn": 1, "seq": start + later},
            {"conversation": "s-long", "turn": 1, "seq": start + later, "part": later},
        ],
        env,
        tool="recall_turn",
    )
    assert shown(opened[0]).startswith(
        f"Turn 1, seq {start} (verbatim):\nTOOL Write → {chunks[0]}\n"
    )
    assert "ZEPHYR-CANARY" not in shown(opened[0])
    assert shown(opened[1]).startswith(
        f"Turn 1, seq {start}, part {later} (verbatim):\nTOOL Write → {chunks[later]}\n"
    )


SPLIT = [
    {"role": "user", "content": "alpha plan"},
    {"role": "assistant", "content": "omega done"},
]


def test_terms_split_across_records_match_the_turn_but_no_record(
    binary: Binary, tmp_path: Path
) -> None:
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    calls: list[dict[str, Any]] = [
        {"query": "alpha omega", "order": "newest"},
        {"query": "alpha omega", "order": "newest", "kinds": ["prompt", "reply"]},
        {"query": "alpha omega", "kinds": ["prompt", "reply"]},
    ]
    with serve(binary) as kept, serve(plain) as legacy:
        for server in (kept, legacy):
            server.post(
                "conversation/append_turn", conversation_id="s-split", messages=SPLIT
            )
        turns = {
            name: server.post("conversation/read_turns", conversation_id="s-split")[
                "turns"
            ][0]
            for name, server in (("kept", kept), ("legacy", legacy))
        }
        answers = {}
        for name, server in (("kept", kept), ("legacy", legacy)):
            env = {"NEOSIAN_CLIENT_TOKEN": server.token}
            answers[name] = [
                shown(a) for a in tools(binary, ["--url", server.url], calls, env)
            ]
    none = (
        "No record holds every term: 'alpha', 'omega'. A record matches on its "
        "own text; try fewer or different terms."
    )
    note = f"[{kinds_note('prompt, reply')}]"
    assert answers["kept"][:2] == [none, f"{note}\n{none}"]
    # The turn search finds the turn; no record of it holds both terms.
    stamp, actor = turns["legacy"]["created_at"], turns["legacy"]["actor"]
    assert answers["legacy"][0] == (
        f"[s-split #1] {stamp} {actor}\n  alpha plan\n"
        '[newest first, at most 10; call recall_turn(n, conversation="<id>") to '
        "re-read a hit verbatim]"
    )
    assert answers["legacy"][1] == f"{note}\n[{DERIVED}]\n{none}"

    # Relevance needs one term: both records, from either store.
    def rows(name: str) -> list[str]:
        at, by = turns[name]["created_at"], turns[name]["actor"]
        return [
            f"[s-split #1.0 prompt] {at} {by}\n  alpha plan",
            f"[s-split #1.1 reply] {at} {by}\n  omega done",
        ]

    kept_rows = answers["kept"][2].split("\n")
    assert kept_rows[0] == note and kept_rows[-1] == RANKED.format(10)
    assert sorted("\n".join(kept_rows[i : i + 2]) for i in (1, 3)) == rows("kept")
    assert answers["legacy"][2] == "\n".join(
        [note, f"[{DERIVED}]", *rows("legacy"), RANKED.format(10)]
    )


def test_an_erased_turn_never_matches(binary: Binary) -> None:
    enable(binary)
    with serve(binary) as kept:
        for draft in ("d1", "d2"):
            items = [record(0, 0, "user", "prompt", f"needle {draft}")]
            write(kept, "s-erase", draft, items, seal=True)
    root = ["--root", str(binary.home)]
    assert [h["turn"] for h in cli(binary, "needle", *root)["hits"]] == [2, 1]
    assert binary.json("redact", "s-erase", "--turns", "2", *root)["count"] == 1
    page = cli(binary, "needle", *root)
    assert [(h["turn"], h["excerpt"]["text"]) for h in page["hits"]] == [
        (1, "needle d1")
    ]
    [answer] = tools(binary, root, [{"query": "needle d2", "order": "newest"}])
    assert shown(answer) == (
        "No record holds every term: 'needle', 'd2'. A record matches on its "
        "own text; try fewer or different terms."
    )


ADMITTED = PREFIX + "a"
PROFILE = (
    BASE
    + '\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\n'
    + f'conversations = ["{ADMITTED}"]\n'
)


def test_two_sources_with_different_authority_see_different_hits(
    binary: Binary,
) -> None:
    enable(binary)
    grants = grant_env(binary.directory / "grants.toml")
    with serve(binary) as open_store:
        for conversation in (ADMITTED, "other-b"):
            items = [record(0, 0, "user", "prompt", f"needle in {conversation}")]
            write(open_store, conversation, "d", items, seal=True)
        whole = {"NEOSIAN_CLIENT_TOKEN": open_store.token}
        everything = cli(
            binary, "needle", "--order", "newest", "--url", open_store.url, env=whole
        )
        assert {h["conversation_id"] for h in everything["hits"]} == {
            ADMITTED,
            "other-b",
        }
    with serve(binary, extra_env=grants) as granted:
        calls: list[dict[str, Any]] = [
            {"query": "needle"},
            {"query": "needle", "conversation": ADMITTED},
            {"query": "needle", "conversation": "other-b"},
        ]
        answers = {}
        with capture(granted) as held:
            for token in ("contract-admin", "reader-token"):
                env = {"NEOSIAN_CLIENT_TOKEN": token}
                answers[token] = tools(
                    binary, ["--url", held.url], calls, env, scope="workspace:portable"
                )
        forbidden = "Tool 'search_history' failed: refused with 403 (forbidden): "
        # Under grants a search names its conversations (ruling R13).
        assert refused(answers["contract-admin"][0]).startswith(forbidden)
        assert shown(answers["contract-admin"][1]).startswith(
            f"[{ADMITTED} #1.0 prompt] "
        )
        assert shown(answers["contract-admin"][2]).startswith("[other-b #1.0 prompt] ")
        assert refused(answers["reader-token"][0]).startswith(forbidden)
        assert shown(answers["reader-token"][1]).startswith(
            f"[{ADMITTED} #1.0 prompt] "
        )
        assert refused(answers["reader-token"][2]).startswith(forbidden)
        # A refusal is never asked again as a turn search.
        assert [route for route, _ in searched(held)] == ["search_records"] * 6
        # A profile sends exactly the conversations its history admits.
        install(binary, PROFILE)
        with capture(granted) as held:
            bind(binary, "--url", held.url, "--token-env", "WORKSPACE_TOKEN")
            page = cli(
                binary,
                "needle",
                "--profile",
                "portable",
                env={"WORKSPACE_TOKEN": "reader-token"},
            )
        assert [h["conversation_id"] for h in page["hits"]] == [ADMITTED]
        assert [
            body for route, body in searched(held) if route == "search_records"
        ] == [
            {
                "query": "needle",
                "conversations": [ADMITTED],
                "order": "relevance",
                "limit": 20,
            }
        ]


def test_a_refusal_after_the_capability_was_advertised_stays_a_refusal(
    binary: Binary,
) -> None:
    enable(binary)
    bound = {"NEOSIAN_SEARCH_MAX_CANDIDATES": "1"}
    with serve(binary, extra_env=bound) as kept:
        for draft in ("d1", "d2"):
            write(
                kept,
                "s-many",
                draft,
                [record(0, 0, "user", "prompt", "common")],
                seal=True,
            )
        env = {"NEOSIAN_CLIENT_TOKEN": kept.token}
        with capture(kept, fail="search_records") as held:
            [answer] = tools(binary, ["--url", held.url], [{"query": "common"}], env)
            human = run(binary, "search", "common", "--url", held.url, env=env)
        injected = "refused with 400 (value_error): injected read failure"
        assert refused(answer) == f"Tool 'search_history' failed: {injected}"
        assert (human.returncode, human.stdout) == (1, "")
        assert human.stderr == f"error: {injected}\n"
        assert [route for route, _ in searched(held)] == ["search_records"] * 2
        with capture(kept) as held:
            [broad] = tools(binary, ["--url", held.url], [{"query": "common"}], env)
            newest = tools(
                binary,
                ["--url", held.url],
                [{"query": "common", "order": "newest"}],
                env,
            )
        message = refused(broad)
        assert "resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded" in message
        assert "Too many records hold one of these terms" in message
        # Newest needs every term in one record and is not bounded this way.
        heads = [
            h.split("]")[0]
            for h in shown(newest[0]).split("\n")
            if h.startswith("[s-many")
        ]
        assert heads == ["[s-many #2.0 prompt", "[s-many #1.0 prompt"]
        assert [route for route, _ in searched(held)] == ["search_records"] * 2


def plain_turns(server: Server) -> list[dict[str, Any]]:
    server.post(
        "conversation/append_turn",
        conversation_id="s-plain",
        messages=[
            {"role": "user", "content": "run the cache check"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "name": "Bash", "arguments": {"cmd": "check"}}
                ],
            },
            {"role": "tool", "content": "cache warm", "tool_call_id": "c1"},
            {"role": "assistant", "content": "The cache is warm."},
        ],
    )
    turns: list[dict[str, Any]] = server.post(
        "conversation/read_turns", conversation_id="s-plain"
    )["turns"]
    return turns


def test_a_server_without_records_keeps_its_turn_answer_and_labels_kinds(
    binary: Binary,
) -> None:
    with serve(binary) as legacy:
        [turn] = plain_turns(legacy)
        env = {"NEOSIAN_CLIENT_TOKEN": legacy.token}
        with capture(legacy) as held:
            answers = tools(
                binary,
                ["--url", held.url],
                [{"query": "cache"}, {"query": "cache", "kinds": ["tool_result"]}],
                env,
            )
            page = cli(binary, "cache", "--url", held.url, env=env)
            derived = cli(
                binary, "cache", "--kind", "tool_result", "--url", held.url, env=env
            )
            human = run(
                binary,
                "search",
                "cache",
                "--kind",
                "tool_result",
                "--url",
                held.url,
                env=env,
            )
        assert "search_records" not in {route for route, _ in searched(held)}
    stamp, actor = turn["created_at"], turn["actor"]
    assert shown(answers[0]) == (
        f"[s-plain #1] {stamp} {actor}\n  run the cache check\n"
        '[best match first, at most 10; call recall_turn(n, conversation="<id>") '
        "to re-read a hit verbatim]"
    )
    result = {
        "conversation_id": "s-plain",
        "turn": 1,
        "seq": 2,
        "message": 2,
        "kind": "tool_result",
        "call_id": "c1",
        "name": "Bash",
        "created_at": stamp,
        "actor": actor,
        "size": 10,
        "excerpt": {"offset": 0, "text": "cache warm"},
    }
    assert shown(answers[1]) == "\n".join(
        [
            f"[{kinds_note('tool_result')}]",
            f"[{DERIVED}]",
            block(result),
            RANKED.format(10),
        ]
    )
    assert page == {
        "query": "cache",
        "conversations": None,
        "limit": 20,
        "client": page["client"],
        "hits": [
            {
                "conversation_id": "s-plain",
                "turn": 1,
                "created_at": stamp,
                "actor": actor,
                "snippet": "run the cache check",
                "score": page["hits"][0]["score"],
            }
        ],
        "order": "relevance",
    }
    assert derived == {
        "query": "cache",
        "conversations": None,
        "kinds": ["tool_result"],
        "limit": 20,
        "client": page["client"],
        "hits": [typed(result)],
        "order": "relevance",
        "approximation": DERIVED,
    }
    assert human.stdout == (
        "order: relevance\n"
        f"kinds: {kinds_note('tool_result')}\n"
        f"approximation: {DERIVED}\n"
        f"{head(result)}  cache warm\n"
    )


def test_kinds_and_the_record_limit_are_checked_on_both_doors(
    binary: Binary, tmp_path: Path
) -> None:
    plain = replace(binary, directory=tmp_path / "plain")
    plain.home.mkdir(parents=True)
    enable(binary)
    for home in (binary, plain):
        answers = tools(
            home,
            ["--root", str(home.home)],
            [
                {"query": "x", "kinds": ["reasoning"]},
                {"query": "x", "kinds": ["attachment", "prompt"]},
                {"query": "x", "kinds": []},
                {"query": "x", "kinds": ["Prompt"]},
            ],
        )
        assert [refused(a) for a in answers] == [
            f"kind 'reasoning' is never searched; kinds takes {NAMES}",
            f"kind 'attachment' is never searched; kinds takes {NAMES}",
            f"kinds needs at least one of {NAMES}",
            f"unknown kind 'Prompt'; kinds takes {NAMES}",
        ]
        result = run(
            home, "search", "x", "--kind", "reasoning", "--root", str(home.home)
        )
        assert (result.returncode, result.stdout) == (2, "")
        assert result.stderr == (
            f"error: --kind: kind 'reasoning' is never searched; kinds takes {NAMES}\n"
        )
        over = run(
            home,
            "search",
            "x",
            "--kind",
            "prompt",
            "--limit",
            "101",
            "--root",
            str(home.home),
        )
        assert (over.returncode, over.stderr) == (
            2,
            "error: --limit must be 1 to 100 for record hits, got 101\n",
        )
    kept = run(binary, "search", "x", "--limit", "101", "--root", str(binary.home))
    assert (kept.returncode, kept.stderr) == (
        2,
        "error: --limit must be 1 to 100 for record hits, got 101\n",
    )
    assert (
        run(
            binary, "search", "x", "--limit", "100", "--root", str(binary.home)
        ).returncode
        == 0
    )
    legacy = run(plain, "search", "x", "--limit", "500", "--root", str(plain.home))
    assert (legacy.returncode, legacy.stdout) == (
        0,
        "order: relevance\nno turn matches any term of 'x'\n",
    )


def send(binary: Binary, body: str, *about: str) -> None:
    sent = binary.json(
        "messages", "send", *SCOPE, "--session", "worker", "--body", body, *about
    )
    assert sent["success"] is True


def test_mailbox_messages_join_record_hits_only_without_kinds(binary: Binary) -> None:
    enable(binary)
    with serve(binary) as kept:
        write(
            kept,
            "s-mail",
            "d",
            [record(0, 0, "user", "prompt", "deploy the cache")],
            seal=True,
        )
    send(binary, "cache deploy review")
    send(binary, "about the deploy", "--about", "s-mail", "--about-turn", "1")
    root = ["--root", str(binary.home)]
    page = cli(binary, "cache", "deploy", *root)
    assert [h.get("type") for h in page["hits"]] == ["record", "message"]
    assert page["hits"][1]["snippet"] == "cache deploy review"
    assert "about the deploy" in page["hits"][0]["annotations"][0]
    # Newest order merges by instant: the message was sent after the turn.
    newest = cli(binary, "cache", "deploy", "--order", "newest", *root)
    assert [h.get("type") for h in newest["hits"]] == ["message", "record"]
    narrowed = cli(binary, "cache", "deploy", "--kind", "prompt", *root)
    assert [h.get("type") for h in narrowed["hits"]] == ["record"]
    assert "about the deploy" in narrowed["hits"][0]["annotations"][0]
    answers = tools(
        binary,
        root,
        [{"query": "cache deploy"}, {"query": "cache deploy", "kinds": ["prompt"]}],
    )
    blocks = shown(answers[0]).split("\n\n")
    assert blocks[0].startswith("[s-mail #1.0 prompt] ")
    assert "about the deploy" in blocks[0]
    assert blocks[1].startswith("[mailbox message, not a record]\n[")
    assert "cache deploy review" in blocks[1]
    assert blocks[-1] == RANKED.format(10)
    labelled = shown(answers[1])
    assert labelled.startswith(f"[{kinds_note('prompt')}]\n[s-mail #1.0 prompt] ")
    assert "[mailbox message, not a record]" not in labelled
    assert "cache deploy review" not in labelled


def test_the_cli_help_names_kind(binary: Binary) -> None:
    result = run(binary, "search", "--help")
    assert "--kind <KIND>" in result.stdout
