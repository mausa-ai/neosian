"""Ranked and time-bounded history search through the agent doors (S12).

`neosian search` and MCP `search_history` rank by default (DESIGN §40.43).
Scores are literal values from an independent Python reference of the pinned
formula over the candidate turns; the binary under test never produced them.
Turns are planted as literal format-1 rows with fixed times, so bounds and
ties are exact.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.contract.profile_support import (
    PREFIX,
    PROFILE,
    bind,
    grant_env,
    install,
    rpc,
    run,
)
from tests.contract.support import ADMIN, Binary, serve
from tests.contract.test_transport import TOKEN, Reply, responder

ACTOR = "agent:fixture"
QUERY = ("Dashboard", "RESPONSE", "time")
UNRANKED = "does not support history-ranking version 1"
RANKED_FOOTER = (
    "[best match first, at most {}; call recall_turn(n, "
    'conversation="<id>") to re-read a hit verbatim]'
)
NEWEST_FOOTER = (
    "[newest first, at most {}; call recall_turn(n, "
    'conversation="<id>") to re-read a hit verbatim]'
)
WINDOW = ("--since", "2026-11-03T09:00:00Z", "--before", "2026-11-05T09:00:00Z")
BOUNDS = {"since": "2026-11-03T09:00:00Z", "before": "2026-11-05T09:00:00Z"}
Rows = list[tuple[str, list[dict[str, Any]]]]


def user(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }


# Searchable text: 'deploy {"target":"dashboard"}\ndeployed dashboard'.
DEPLOY: list[dict[str, Any]] = [
    {
        "role": "assistant",
        "content": None,
        "reasoning": None,
        "tool_calls": [
            {"id": "call-1", "name": "deploy", "arguments": {"target": "dashboard"}}
        ],
        "tool_call_id": None,
    },
    {
        "role": "tool",
        "content": "deployed dashboard",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": "call-1",
    },
]
CORPUS: dict[str, Rows] = {
    "alpha-session": [
        ("2026-11-01T09:00:00Z", [user("The dashboard API response time is 300ms")]),
        ("2026-11-02T09:00:00Z", [user("Lunch at noon today")]),
        (
            "2026-11-03T09:00:00Z",
            [user("Dashboard API improved to 250ms after caching")],
        ),
        ("2026-11-06T09:00:00Z", [user("same words")]),
        ("2026-11-06T09:00:00Z", [user("same words")]),
    ],
    "beta-session": [
        ("2026-11-04T09:00:00Z", [user("The response time target is 200ms")]),
        ("2026-11-05T09:00:00Z", DEPLOY),
        ("2026-11-06T09:00:00Z", [user("same words")]),
    ],
}
OLD: dict[str, Rows] = {
    "old-session": [
        ("1970-01-02T00:00:00Z", [user("Legacy dashboard response time notes")])
    ]
}
HIDDEN: dict[str, Rows] = {
    "other-hidden": [
        ("2026-11-07T09:00:00Z", [user("Hidden dashboard response time notes")])
    ]
}
UNICODE: dict[str, Rows] = {
    "unicode-session": [
        ("2026-11-01T09:00:00Z", [user("Élan vital: ÉLAN")]),
        ("2026-11-02T09:00:00Z", [user("élan and Straße")]),
        ("2026-11-03T09:00:00Z", [user("nothing here")]),
    ]
}

# (conversation, turn, score) from the independent reference.
RANKED = [
    ("alpha-session", 1, 1769128),
    ("beta-session", 1, 1513074),
    ("beta-session", 2, 469736),
    ("alpha-session", 3, 344779),
]
TIED = [
    ("beta-session", 3, 133531),
    ("alpha-session", 5, 133531),
    ("alpha-session", 4, 133531),
]
BETA = [("beta-session", 1, 1499925), ("beta-session", 2, 905895)]
IN_WINDOW = [("beta-session", 1, 1479404), ("alpha-session", 3, 652106)]
SINCE = [
    ("beta-session", 1, 2150145),
    ("beta-session", 2, 621292),
    ("alpha-session", 3, 456660),
]
BEFORE = [("alpha-session", 1, 863046)]
WITH_OLD = [
    ("old-session", 1, 1429359),
    ("alpha-session", 1, 1371229),
    ("beta-session", 1, 1165313),
    ("beta-session", 2, 375686),
    ("alpha-session", 3, 274878),
]
ERASED = [
    ("alpha-session", 1, 2182458),
    ("beta-session", 2, 179432),
    ("alpha-session", 3, 132715),
]


def plant(root: Path, corpus: dict[str, Rows], prefix: str = "") -> None:
    for conversation, rows in corpus.items():
        folder = root / "conversations" / (prefix + conversation)
        folder.mkdir(parents=True)
        lines = [
            json.dumps(
                {
                    "neosian_format": 1,
                    "turn": number,
                    "messages": messages,
                    "created_at": at,
                    "actor": ACTOR,
                    "redacted": False,
                }
            )
            for number, (at, messages) in enumerate(rows, 1)
        ]
        (folder / "turns.jsonl").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8"
        )


def search(binary: Binary, *args: str, env: dict[str, str] | None = None) -> Any:
    result = run(binary, "search", *args, "--json", env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def scored(page: Any) -> list[tuple[str, int, int]]:
    assert page["order"] == "relevance" and "fallback" not in page
    return [(h["conversation_id"], h["turn"], h["score"]) for h in page["hits"]]


def listed(page: Any) -> list[tuple[str, int]]:
    assert all("score" not in h for h in page["hits"])
    return [(h["conversation_id"], h["turn"]) for h in page["hits"]]


def pairs(triples: list[tuple[str, int, int]]) -> list[tuple[str, int]]:
    return [(conversation, turn) for conversation, turn, _ in triples]


def identities(page: Any) -> list[str]:
    return [
        (
            h["snippet"]
            if h.get("type") == "message"
            else f"{h['conversation_id']} #{h['turn']}"
        )
        for h in page["hits"]
    ]


def mcp(
    binary: Binary,
    calls: list[dict[str, Any]],
    *flags: str,
    env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
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
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    for number, arguments in enumerate(calls, 2):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": "search_history", "arguments": arguments},
            }
        )
    process = run(
        binary,
        "mcp",
        *(flags or ("--scope", "user:contract")),
        "--actor",
        "mcp:contract",
        env=env,
        stdin="".join(json.dumps(r) + "\n" for r in requests),
    )
    assert process.returncode == 0, process.stderr
    answers = [json.loads(line) for line in process.stdout.splitlines()]
    assert [a["id"] for a in answers] == list(range(len(calls) + 2))
    tools = {tool["name"]: tool for tool in answers[1]["result"]["tools"]}
    return tools, [a["result"] for a in answers[2:]]


def answer(result: Any) -> str:
    assert result.get("isError") is not True, result
    return str(result["content"][0]["text"])


def refusal(result: Any) -> str:
    assert result["isError"] is True, result
    return "\n".join(str(item["text"]) for item in result["content"])


def nothing_ranked(text: str, *terms: str) -> None:
    """An empty relevance answer names its order; fewer terms cannot help."""
    listed = ", ".join(f"'{term}'" for term in terms)
    assert text.startswith(f"No turn matches any term: {listed}. "), text
    assert "best match" in text and "one term is enough" in text
    assert "fewer" not in text


HEADER = re.compile(r"^\[(\S+) #(\d+)\] ", re.MULTILINE)


def headers(text: str) -> list[tuple[str, int]]:
    return [(found[1], int(found[2])) for found in HEADER.finditer(text)]


def blocks(text: str) -> list[str]:
    """A merged MCP answer's blocks: turn headers, message bodies, footers."""
    named = []
    for block in text.split("\n\n"):
        lines = block.strip("\n").splitlines()
        if not lines:
            continue
        head = HEADER.match(lines[0])
        if head:
            named.append(f"{head[1]} #{head[2]}")
        elif " occurrence=" in lines[0]:
            named.append(lines[1])
        else:
            named.append(lines[0])
    return named


def test_cli_ranks_by_default_and_names_the_order(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    page = search(binary, *QUERY)
    assert set(page) == {"query", "conversations", "limit", "client", "hits", "order"}
    assert (page["query"], page["conversations"], page["limit"]) == (
        "Dashboard RESPONSE time",
        None,
        20,
    )
    assert scored(page) == RANKED
    assert page["hits"][0] == {
        "conversation_id": "alpha-session",
        "turn": 1,
        "created_at": "2026-11-01T09:00:00Z",
        "actor": ACTOR,
        "snippet": "The dashboard API response time is 300ms",
        "score": 1769128,
    }
    # Tool calls count as searchable text; the snippet keeps version 1's rule.
    assert page["hits"][2]["snippet"] == 'deploy {"target":"dashboard"}'
    human = binary.run("search", *QUERY)
    assert (human.returncode, human.stderr) == (0, "")
    assert human.stdout.splitlines() == [
        "order: relevance",
        "[alpha-session #1] 2026-11-01T09:00:00Z agent:fixture  "
        "The dashboard API response time is 300ms",
        "[beta-session #1] 2026-11-04T09:00:00Z agent:fixture  "
        "The response time target is 200ms",
        "[beta-session #2] 2026-11-05T09:00:00Z agent:fixture  "
        'deploy {"target":"dashboard"}',
        "[alpha-session #3] 2026-11-03T09:00:00Z agent:fixture  "
        "Dashboard API improved to 250ms after caching",
    ]
    assert scored(search(binary, *QUERY, "--limit", "2")) == RANKED[:2]
    # Relevance needs one term; newest needs every term.
    assert scored(search(binary, "lunch", "zebra")) == [("alpha-session", 2, 287682)]
    assert search(binary, "lunch", "zebra", "--order", "newest")["hits"] == []
    empty = binary.run("search", "zebra")
    assert empty.stdout == "order: relevance\nno turn matches any term of 'zebra'\n"


def test_ties_follow_the_version_1_total_order(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    # One instant, equal scores: conversation id, then turn, each descending.
    assert scored(search(binary, "same")) == TIED
    assert scored(search(binary, "same", "--limit", "2")) == TIED[:2]
    newest = search(binary, "same", "--order", "newest")
    assert listed(newest) == pairs(TIED)
    _, results = mcp(binary, [{"query": "same"}, {"query": "same", "limit": 1}])
    assert headers(answer(results[0])) == pairs(TIED)
    assert headers(answer(results[1])) == pairs(TIED[:1])
    assert answer(results[1]).endswith("\n" + RANKED_FOOTER.format(1))


def test_explicit_newest_without_bounds_keeps_version_1_output(
    binary: Binary,
) -> None:
    plant(binary.home, CORPUS)
    assert search(binary, "dashboard", "--order", "newest") == {
        "query": "dashboard",
        "conversations": None,
        "limit": 20,
        "client": None,
        "hits": [
            {
                "conversation_id": "beta-session",
                "turn": 2,
                "created_at": "2026-11-05T09:00:00Z",
                "actor": ACTOR,
                "snippet": 'deploy {"target":"dashboard"}',
            },
            {
                "conversation_id": "alpha-session",
                "turn": 3,
                "created_at": "2026-11-03T09:00:00Z",
                "actor": ACTOR,
                "snippet": "Dashboard API improved to 250ms after caching",
            },
            {
                "conversation_id": "alpha-session",
                "turn": 1,
                "created_at": "2026-11-01T09:00:00Z",
                "actor": ACTOR,
                "snippet": "The dashboard API response time is 300ms",
            },
        ],
    }
    human = binary.run("search", "dashboard", "--order", "newest")
    assert human.stdout.splitlines() == [
        "[beta-session #2] 2026-11-05T09:00:00Z agent:fixture  "
        'deploy {"target":"dashboard"}',
        "[alpha-session #3] 2026-11-03T09:00:00Z agent:fixture  "
        "Dashboard API improved to 250ms after caching",
        "[alpha-session #1] 2026-11-01T09:00:00Z agent:fixture  "
        "The dashboard API response time is 300ms",
    ]
    none = binary.run("search", "zebra", "--order", "newest")
    assert none.stdout == "no turn matches every term of 'zebra'\n"
    _, results = mcp(
        binary,
        [
            {"query": "dashboard", "order": "newest"},
            {"query": "zebra", "order": "newest"},
        ],
    )
    assert answer(results[0]) == "\n".join(
        [
            "[beta-session #2] 2026-11-05T09:00:00Z agent:fixture",
            '  deploy {"target":"dashboard"}',
            "[alpha-session #3] 2026-11-03T09:00:00Z agent:fixture",
            "  Dashboard API improved to 250ms after caching",
            "[alpha-session #1] 2026-11-01T09:00:00Z agent:fixture",
            "  The dashboard API response time is 300ms",
            NEWEST_FOOTER.format(10),
        ]
    )
    assert answer(results[1]) == (
        "No turn matches every term: 'zebra'. Try fewer or different terms."
    )


def test_mcp_ranks_by_default_and_describes_its_inputs(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    tools, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time"},
            {"query": "Dashboard RESPONSE time", "limit": 2},
            {"query": "lunch zebra"},
            {"query": "zebra"},
            {"query": "dashboard", "order": "score"},
            {"query": "  "},
            {"query": "  ", "order": "newest"},
        ],
    )
    schema = tools["search_history"]["inputSchema"]["properties"]
    assert schema["order"]["anyOf"][0]["enum"] == ["relevance", "newest"]
    assert {"since", "before"} <= set(schema)
    assert answer(results[0]) == "\n".join(
        [
            "[alpha-session #1] 2026-11-01T09:00:00Z agent:fixture",
            "  The dashboard API response time is 300ms",
            "[beta-session #1] 2026-11-04T09:00:00Z agent:fixture",
            "  The response time target is 200ms",
            "[beta-session #2] 2026-11-05T09:00:00Z agent:fixture",
            '  deploy {"target":"dashboard"}',
            "[alpha-session #3] 2026-11-03T09:00:00Z agent:fixture",
            "  Dashboard API improved to 250ms after caching",
            RANKED_FOOTER.format(10),
        ]
    )
    assert headers(answer(results[1])) == pairs(RANKED[:2])
    assert answer(results[1]).endswith("\n" + RANKED_FOOTER.format(2))
    assert headers(answer(results[2])) == [("alpha-session", 2)]
    nothing_ranked(answer(results[3]), "zebra")
    assert "order" in refusal(results[4])
    # A blank query: the hint follows the order; newest keeps version 1's.
    blank = refusal(results[5])
    assert "one term is enough" in blank and "every term" not in blank
    assert refusal(results[6]) == (
        "Give at least one search term\n"
        "A turn matches when it holds every term, case-insensitively."
    )


# The replay corpus maps search_history's text back to version 1, so the
# current text is pinned here, whole.
DESCRIPTION = (
    "Search every recorded conversation for turns by their words "
    "(case-insensitive substrings, separated by spaces): message text, tool "
    "calls with their arguments, and tool results. Results rank best match "
    "first by default: a turn needs only one term and rarer terms count more, "
    "so add synonyms to one query. order=newest lists turns holding every term, "
    "newest first. since and before limit turn time (UTC, ending in Z); before "
    "is an exact as-of cutoff. Each hit names the conversation and turn, e.g. "
    "[cc-7f3a #2], with a snippet around the match; pass that id and number to "
    "recall_turn to read the turn verbatim. Use it when the memory index and "
    "the 'where we left off' block do not say where something happened, or "
    "when a fact lives in a session older than the ones shown. Pass "
    "conversation=<id> to search one session; limit caps the hits (default 10, "
    "at most 50). A later turn can supersede an earlier one, so check newer "
    "hits before relying on an older fact."
)
PARAMETERS = {
    "query": "One or more terms separated by spaces, case-insensitively; "
    "relevance needs one, newest every one.",
    "conversation": "The id of one conversation to search, as the sessions/ "
    "document names it; omit for every recorded conversation.",
    "limit": "How many hits to return (default 10, at most 50).",
    "order": "relevance (the default when omitted): best match first; newest: "
    "every term, newest first.",
    "since": "Only turns at or after this UTC instant, RFC 3339 ending in Z.",
    "before": "Only turns before this UTC instant, RFC 3339 ending in Z: an "
    "exact as-of cutoff.",
}


def test_search_history_text_is_pinned_whole(binary: Binary) -> None:
    tools, _ = mcp(binary, [])
    tool = tools["search_history"]
    assert tool["description"] == DESCRIPTION
    properties = tool["inputSchema"]["properties"]
    assert list(properties) == list(PARAMETERS)
    assert {k: v["description"] for k, v in properties.items()} == PARAMETERS
    assert properties["order"]["anyOf"] == [
        {"enum": ["relevance", "newest"], "type": "string"},
        {"type": "null"},
    ]
    assert tool["inputSchema"]["required"] == ["query"]


def test_relevance_counts_unicode_lowercase_on_files(binary: Binary) -> None:
    plant(binary.home, UNICODE)
    # 'ÉLAN' counts as 'élan' twice in turn 1; lengths are folded scalars.
    two = search(binary, "ÉLAN", "Straße")
    assert scored(two) == [
        ("unicode-session", 2, 887176),
        ("unicode-session", 1, 248438),
    ]
    one = search(binary, "ÉLAN")
    assert scored(one) == [
        ("unicode-session", 1, 248438),
        ("unicode-session", 2, 184760),
    ]
    _, results = mcp(binary, [{"query": "ÉLAN Straße"}])
    assert headers(answer(results[0])) == [
        ("unicode-session", 2),
        ("unicode-session", 1),
    ]


def test_bounds_take_since_inclusive_and_before_exclusive(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    assert scored(search(binary, *QUERY, *WINDOW)) == IN_WINDOW
    assert scored(search(binary, *QUERY, "--since", BOUNDS["since"])) == SINCE
    assert scored(search(binary, *QUERY, "--before", BOUNDS["since"])) == BEFORE
    # Bounds are instants: a microsecond later leaves the 09:00 turn out.
    later = search(binary, *QUERY, "--since", "2026-11-03T09:00:00.000001Z")
    assert scored(later) == BETA
    future = search(binary, *QUERY, "--since", "2999-01-01T00:00:00Z")
    assert (future["order"], future["hits"]) == ("relevance", [])
    newest = ("dashboard", "--order", "newest")
    since = search(binary, *newest, "--since", BOUNDS["since"])
    assert listed(since) == [("beta-session", 2), ("alpha-session", 3)]
    assert listed(search(binary, *newest, *WINDOW)) == [("alpha-session", 3)]
    before = search(binary, *newest, "--before", BOUNDS["since"])
    assert listed(before) == [("alpha-session", 1)]
    _, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time", **BOUNDS},
            {"query": "dashboard", "order": "newest", "since": BOUNDS["since"]},
            {"query": "dashboard", "order": "newest", "before": BOUNDS["since"]},
            {"query": "dashboard", "order": "newest", **BOUNDS},
        ],
    )
    assert headers(answer(results[0])) == pairs(IN_WINDOW)
    assert answer(results[0]).endswith("\n" + RANKED_FOOTER.format(10))
    assert headers(answer(results[1])) == [("beta-session", 2), ("alpha-session", 3)]
    assert answer(results[1]).endswith("\n" + NEWEST_FOOTER.format(10))
    assert headers(answer(results[2])) == [("alpha-session", 1)]
    assert headers(answer(results[3])) == [("alpha-session", 3)]


INSTANT = "must be a UTC RFC 3339 instant ending in Z"
PRECEDE = "since must precede before"


@pytest.mark.parametrize("order", ["relevance", "newest"])
@pytest.mark.parametrize(
    "bounds,reason",
    [
        ({"since": "yesterday"}, "since " + INSTANT),
        ({"before": "2026-11-03T10:00:00+01:00"}, "before " + INSTANT),
        ({"since": "2026-11-03"}, "since " + INSTANT),
        ({"before": "2026-11-03t09:00:00z"}, "before " + INSTANT),
        ({"since": "2026-11-03T09:00:00Z", "before": "2026-11-03T09:00:00Z"}, PRECEDE),
        ({"since": "2026-11-04T00:00:00Z", "before": "2026-11-03T00:00:00Z"}, PRECEDE),
    ],
)
def test_bad_bounds_are_refused_on_both_doors(
    binary: Binary, order: str, bounds: dict[str, str], reason: str
) -> None:
    plant(binary.home, CORPUS)
    flags = [part for name, value in bounds.items() for part in ("--" + name, value)]
    args = ("dashboard", "--order", order, *flags)
    machine = binary.run("search", *args, "--json")
    assert machine.returncode == 2
    assert json.loads(machine.stdout) == {"error": "usage", "hint": reason}
    human = binary.run("search", *args)
    assert (human.returncode, human.stdout) == (2, "")
    assert reason in human.stderr
    _, results = mcp(binary, [{"query": "dashboard", "order": order, **bounds}])
    assert reason in refusal(results[0])


def test_narrowing_ranks_within_the_selected_conversation(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    page = search(binary, *QUERY, "--conversation", "beta-session")
    assert page["conversations"] == ["beta-session"]
    # Statistics come from the selected candidates only.
    assert scored(page) == BETA
    both = ("--conversation", "beta-session", "--conversation", "alpha-session")
    assert scored(search(binary, *QUERY, *both)) == RANKED
    absent = search(binary, *QUERY, "--conversation", "gamma-session")
    assert absent["hits"] == []
    _, results = mcp(
        binary, [{"query": "Dashboard RESPONSE time", "conversation": "beta-session"}]
    )
    assert headers(answer(results[0])) == pairs(BETA)


def test_erased_and_pruned_turns_stay_out_of_the_ranking(binary: Binary) -> None:
    plant(binary.home, {**CORPUS, **OLD})
    assert scored(search(binary, *QUERY)) == WITH_OLD
    # Input only: a cutoff at 2000-01-01 prunes the 1970 session, not 2026's.
    days = (datetime.now(UTC) - datetime(2000, 1, 1, tzinfo=UTC)).days
    pruned = binary.json("prune", "--older-than", f"{days}d")
    assert [c["conversation_id"] for c in pruned["conversations"]] == ["old-session"]
    assert binary.json("redact", "beta-session", "--turns", "1")["count"] == 1
    assert scored(search(binary, *QUERY)) == ERASED
    assert search(binary, "legacy")["hits"] == []
    since = ("--since", "1970-01-01T00:00:00Z")
    assert search(binary, "legacy", *since)["hits"] == []
    newest = search(binary, "response", "time", "--order", "newest", *since)
    assert listed(newest) == [("alpha-session", 1)]
    _, results = mcp(
        binary, [{"query": "Dashboard RESPONSE time"}, {"query": "legacy notes"}]
    )
    assert headers(answer(results[0])) == pairs(ERASED)
    nothing_ranked(answer(results[1]), "legacy", "notes")


def send(binary: Binary, body: str, *about: str) -> None:
    sent = binary.json(
        "messages", "send", "--scope", "user:contract", "--session", "worker",
        "--body", body, *about,
    )  # fmt: skip
    assert sent["success"] is True


def test_mailbox_hits_keep_every_term_and_follow_the_ranked_turns(
    binary: Binary,
) -> None:
    plant(binary.home, CORPUS)
    send(binary, "dashboard response time review")
    send(binary, "dashboard only")
    about = ("--about", "beta-session", "--about-turn", "2")
    send(binary, "Response time dashboard followup", *about)
    turns = ["alpha-session #1", "beta-session #1", "beta-session #2"]
    turns.append("alpha-session #3")
    followup, review = (
        "Response time dashboard followup",
        "dashboard response time review",
    )
    page = search(binary, *QUERY)
    assert page["order"] == "relevance"
    # "dashboard only" lacks two terms: a message still needs every term.
    assert identities(page) == turns + [followup, review]
    assert [h.get("score") for h in page["hits"]] == [
        1769128,
        1513074,
        469736,
        344779,
        None,
        None,
    ]
    assert followup in page["hits"][2]["annotations"][0]
    assert identities(search(binary, *QUERY, "--limit", "5")) == turns + [followup]
    assert identities(search(binary, *QUERY, "--limit", "3")) == turns[:3]
    narrowed = search(binary, *QUERY, "--conversation", "beta-session")
    assert identities(narrowed) == ["beta-session #1", "beta-session #2", followup]
    # Version 1 merges by time; the messages are newer than every planted turn.
    newest = search(binary, "dashboard", "--order", "newest")
    assert identities(newest) == [
        followup,
        "dashboard only",
        review,
        "beta-session #2",
        "alpha-session #3",
        "alpha-session #1",
    ]
    human = binary.run("search", *QUERY)
    assert human.stdout.splitlines()[0] == "order: relevance"
    _, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time"},
            {"query": "Dashboard RESPONSE time", "limit": 5},
            {"query": "dashboard", "order": "newest"},
        ],
    )
    assert blocks(answer(results[0])) == turns + [
        followup,
        review,
        RANKED_FOOTER.format(10),
    ]
    assert blocks(answer(results[1])) == turns + [followup, RANKED_FOOTER.format(5)]
    assert blocks(answer(results[2])) == [
        followup,
        "dashboard only",
        review,
        "beta-session #2",
        "alpha-session #3",
        "alpha-session #1",
    ]


def test_mcp_relevance_names_its_order_whatever_the_mailbox_holds(
    binary: Binary,
) -> None:
    plant(binary.home, CORPUS)
    ranked = [f"{c} #{t}" for c, t in pairs(RANKED)] + [RANKED_FOOTER.format(10)]
    newest = ["beta-session #2", "alpha-session #3", "alpha-session #1"]
    calls: list[dict[str, Any]] = [
        {"query": "Dashboard RESPONSE time"},
        {"query": "dashboard", "order": "newest"},
        {"query": "zebra"},
    ]
    # Any message switches the answer to the merged form, matching or not.
    send(binary, "Lunch moved to one")
    _, results = mcp(binary, calls)
    assert blocks(answer(results[0])) == ranked
    assert blocks(answer(results[1])) == newest
    assert "best match" not in answer(results[1])
    nothing_ranked(answer(results[2]), "zebra")
    send(binary, "dashboard response time review")
    _, results = mcp(binary, calls)
    review = "dashboard response time review"
    assert blocks(answer(results[0])) == [*ranked[:-1], review, ranked[-1]]
    assert blocks(answer(results[1])) == [review, *newest]
    assert "best match" not in answer(results[1])


def plant_message(
    root: Path, number: int, at: str, body: str, about: tuple[str, int] | None = None
) -> None:
    """A literal format-1 mailbox document in `user:contract`, made at `at`."""
    identity = f"00000000-0000-4000-8000-{number:012d}"
    record = {
        "format": 1,
        "id": identity,
        "scope": "user:contract",
        "conversation": None,
        "turn": None,
        "source": None,
        "about": about[0] if about else None,
        "about_turn": about[1] if about else None,
        "actor": "cli:fixture",
        "body": body,
        "created_at": at,
        "due_at": at,
        "delivery": "next_activity",
        "actionable": False,
        "occurrence": 1,
        "status": "open",
        "read_by": None,
        "read_at": None,
        "owner": None,
        "claim_token": None,
        "claim_until": None,
        "outcome": None,
        "wake_owner": None,
        "wake_token": None,
        "wake_until": None,
        "wake_accepted": False,
    }
    folder = root / "user%3Acontract" / "documents" / "messages"
    folder.mkdir(parents=True, exist_ok=True)
    front = (
        f"---\nneosian_format: 1\nversion: 1\ncreated_at: '{at}'\n"
        f"updated_at: '{at}'\nactor: cli:fixture\n---\n"
    )
    (folder / f"{identity}.md").write_text(
        front + json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )


def test_bounds_limit_message_hits_by_their_creation_instant(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    since, before = "2026-11-02T12:00:00Z", "2026-11-05T09:00:00Z"
    early = "dashboard response time a microsecond early"
    start = "dashboard response time at the start"
    last = "dashboard response time a microsecond before the cutoff"
    cutoff = "dashboard response time at the cutoff"
    plant_message(binary.home, 1, "2026-11-02T11:59:59.999999Z", early)
    plant_message(binary.home, 2, since, start)
    plant_message(binary.home, 3, "2026-11-05T08:59:59.999999Z", last)
    plant_message(binary.home, 4, before, cutoff)
    flags = ("--since", since, "--before", before)
    # Unbounded, all four are hits; the same turns form the window's candidates.
    assert identities(search(binary, *QUERY))[4:] == [cutoff, last, start, early]
    ranked = search(binary, *QUERY, *flags)
    assert identities(ranked) == ["beta-session #1", "alpha-session #3", last, start]
    assert [h.get("score") for h in ranked["hits"]] == [1479404, 652106, None, None]
    newest = search(binary, "dashboard", "--order", "newest", *flags)
    assert identities(newest) == [last, "alpha-session #3", start]
    _, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time", "since": since, "before": before},
            {"query": "dashboard", "order": "newest", "since": since, "before": before},
            {"query": "dashboard", "order": "newest"},
        ],
    )
    assert blocks(answer(results[0])) == [
        "beta-session #1",
        "alpha-session #3",
        last,
        start,
        RANKED_FOOTER.format(10),
    ]
    assert blocks(answer(results[1])) == [last, "alpha-session #3", start]
    # Explicit newest without bounds keeps version 1: every message stays.
    assert {early, start, last, cutoff} <= set(blocks(answer(results[2])))


def test_bounds_limit_the_annotations_under_a_turn(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    since, before = "2026-11-02T12:00:00Z", "2026-11-05T09:00:00Z"
    inside, after = "note written inside the window", "note written at the cutoff"
    turn = ("alpha-session", 3)
    plant_message(binary.home, 1, "2026-11-04T00:00:00Z", inside, about=turn)
    plant_message(binary.home, 2, before, after, about=turn)
    flags = ("--since", since, "--before", before)

    def notes(page: Any) -> list[str]:
        (hit,) = [h for h in page["hits"] if (h["conversation_id"], h["turn"]) == turn]
        return [note.splitlines()[1] for note in hit.get("annotations", [])]

    assert notes(search(binary, *QUERY, *flags)) == [inside]
    assert notes(search(binary, "dashboard", "--order", "newest", *flags)) == [inside]
    # Without bounds, version 1 shows both notes.
    assert notes(search(binary, "dashboard", "--order", "newest")) == [inside, after]
    _, results = mcp(
        binary,
        [
            {"query": "Dashboard RESPONSE time", "since": since, "before": before},
            {"query": "dashboard", "order": "newest", "since": since, "before": before},
            {"query": "dashboard", "order": "newest"},
        ],
    )
    for bounded in results[:2]:
        assert inside in answer(bounded) and after not in answer(bounded)
    assert inside in answer(results[2]) and after in answer(results[2])


def test_state_process_ranks_like_the_file_store(binary: Binary) -> None:
    plant(binary.home, CORPUS)
    env = {"NEOSIAN_CLIENT_TOKEN": ADMIN}
    with serve(binary) as server:
        remote = ("--url", server.url)
        assert scored(search(binary, *QUERY, *remote, env=env)) == RANKED
        assert scored(search(binary, *QUERY, *remote, *WINDOW, env=env)) == IN_WINDOW
        assert scored(search(binary, "same", *remote, env=env)) == TIED
        narrowed = search(binary, *QUERY, *remote, "--conversation", "beta-session", env=env)  # fmt: skip
        assert scored(narrowed) == BETA
        newest = ("dashboard", "--order", "newest", "--before", BOUNDS["since"])
        assert listed(search(binary, *newest, *remote, env=env)) == [
            ("alpha-session", 1)
        ]
        _, results = mcp(
            binary,
            [{"query": "Dashboard RESPONSE time"}],
            *remote,
            "--scope",
            "user:contract",
            env=env,
        )
        assert headers(answer(results[0])) == pairs(RANKED)
        assert answer(results[0]).endswith("\n" + RANKED_FOOTER.format(10))


def wire_turn(conversation: str, number: int, at: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "turn": number,
        "messages": [user("needle here")],
        "created_at": at,
        "actor": None,
        "redacted": False,
    }


def fake_store(
    capabilities: dict[str, Any], page: Any, observed: list[dict[str, Any]]
) -> Reply:
    """A state process that answers history searches with a fixed page."""

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, **capabilities}
        if path == "/v1/conversation/search_turns":
            observed.append(body)
            return 200, page
        if path == "/v1/store/scopes":
            return 200, {"scopes": []}
        if path == "/v1/memory/list_documents":
            return 200, {"entries": [], "next_cursor": None}
        if path == "/v1/memory/read":
            return 200, {"document": None}
        return 404, {"error": {"code": "not_found", "message": path}}

    return reply


T1 = wire_turn("s1", 1, "2026-11-01T10:00:00Z")
T2 = wire_turn("s1", 2, "2026-11-02T10:00:00Z")
ERASED_TURN = {**wire_turn("s1", 3, "2026-11-03T10:00:00Z"), "messages": []}
ERASED_TURN["redacted"] = True
RANKING = {"history_ranking": {"version": 1}}
CLIENT = {"NEOSIAN_CLIENT_TOKEN": TOKEN}


def test_version_1_history_falls_back_only_for_the_implicit_order(
    binary: Binary,
) -> None:
    observed: list[dict[str, Any]] = []
    with responder(fake_store({}, {"turns": [T1]}, observed)) as (url, _):
        remote = ("needle", "--url", url)
        implicit = search(binary, *remote, env=CLIENT)
        assert implicit["order"] == "newest"
        assert UNRANKED in implicit["fallback"] and "newest" in implicit["fallback"]
        assert listed(implicit) == [("s1", 1)]
        assert observed == [{"query": "needle", "conversations": None, "limit": 20}]
        human = run(binary, "search", *remote, env=CLIENT).stdout.splitlines()
        assert human[0].startswith("fallback: ") and UNRANKED in human[0]
        assert "order: relevance" not in human
        explicit = search(binary, *remote, "--order", "newest", env=CLIENT)
        assert "fallback" not in explicit and "order" not in explicit
        observed.clear()
        for refused in [
            ["--order", "relevance"],
            ["--since", "2026-11-01T00:00:00Z"],
            ["--order", "newest", "--before", "2026-11-02T00:00:00Z"],
        ]:
            result = run(binary, "search", *remote, *refused, "--json", env=CLIENT)
            assert result.returncode == 1 and UNRANKED in result.stderr
        assert observed == []
        _, results = mcp(
            binary,
            [
                {"query": "needle"},
                {"query": "needle", "order": "newest"},
                {"query": "needle", "order": "relevance"},
                {"query": "needle", "before": "2026-11-02T00:00:00Z"},
            ],
            "--url",
            url,
            "--scope",
            "user:test",
            env=CLIENT,
        )
        note, *rest = answer(results[0]).splitlines()
        assert UNRANKED in note and "newest first" in note
        assert headers("\n".join(rest)) == [("s1", 1)]
        assert rest[-1] == NEWEST_FOOTER.format(10)
        assert UNRANKED not in answer(results[1])
        assert UNRANKED in refusal(results[2]) and UNRANKED in refusal(results[3])
        assert observed == [{"query": "needle", "conversations": None, "limit": 10}] * 2


def test_profile_history_without_ranking_falls_back_only_when_implicit(
    binary: Binary,
) -> None:
    observed: list[dict[str, Any]] = []
    scoped = {"authorization": {"version": 1, "mode": "scoped"}}
    with responder(fake_store(scoped, {"turns": [T1]}, observed)) as (url, _):
        install(binary, history('conversations = ["s1"]\n'))
        bind(binary, "--url", url, "--token-env", "WORKSPACE_TOKEN")
        token = {"WORKSPACE_TOKEN": TOKEN}
        profile = ("needle", "--profile", "portable")
        implicit = search(binary, *profile, env=token)
        assert implicit["order"] == "newest" and UNRANKED in implicit["fallback"]
        assert listed(implicit) == [("s1", 1)]
        assert observed == [{"query": "needle", "conversations": ["s1"], "limit": 20}]
        observed.clear()
        for refused in [
            ["--order", "relevance"],
            ["--before", "2026-11-02T00:00:00Z"],
            ["--order", "newest", "--since", "2026-11-01T00:00:00Z"],
        ]:
            result = run(binary, "search", *profile, *refused, "--json", env=token)
            assert result.returncode == 1 and UNRANKED in result.stderr
        answers = rpc(
            binary,
            [
                ("search_history", {"query": "needle"}),
                ("search_history", {"query": "needle", "order": "relevance"}),
                (
                    "search_history",
                    {"query": "needle", "since": "2026-11-01T00:00:00Z"},
                ),
            ],
            env=token,
        )
        note, *rest = answer(answers[2]["result"]).splitlines()
        assert UNRANKED in note and "newest first" in note
        assert headers("\n".join(rest)) == [("s1", 1)]
        assert rest[-1] == NEWEST_FOOTER.format(10)
        assert UNRANKED in refusal(answers[3]["result"])
        assert UNRANKED in refusal(answers[4]["result"])
        assert observed == [{"query": "needle", "conversations": ["s1"], "limit": 10}]


def test_validated_ranked_pages_are_trusted(binary: Binary) -> None:
    observed: list[dict[str, Any]] = []
    # Equal scores list the newer turn first, as the total order requires.
    page = {"turns": [T2, T1], "scores": [7, 7], "order": "relevance"}
    bounds = {"since": "2026-11-01T10:00:00Z", "before": "2026-11-03T00:00:00Z"}
    with responder(fake_store(RANKING, page, observed)) as (url, _):
        flags = ("--since", bounds["since"], "--before", bounds["before"])
        found = search(binary, "Needle", "--url", url, *flags, env=CLIENT)
        assert scored(found) == [("s1", 2, 7), ("s1", 1, 7)]
        assert observed == [
            {
                "query": "needle",
                "conversations": None,
                "limit": 20,
                "order": "relevance",
                **bounds,
            }
        ]
        _, results = mcp(
            binary,
            [{"query": "needle", **bounds}],
            "--url",
            url,
            "--scope",
            "user:test",
            env=CLIENT,
        )
        assert headers(answer(results[0])) == [("s1", 2), ("s1", 1)]
        assert answer(results[0]).endswith("\n" + RANKED_FOOTER.format(10))
        assert observed[-1]["order"] == "relevance" and observed[-1]["limit"] == 10


BOUNDED_NEWEST = ("--order", "newest", "--since", "2026-11-01T00:00:00Z")


@pytest.mark.parametrize(
    "flags,page",
    [
        pytest.param((), {"turns": [T1], "order": "relevance"}, id="no-scores"),
        pytest.param(
            (), {"turns": [T2, T1], "scores": [5], "order": "relevance"}, id="short"
        ),
        pytest.param(
            (), {"turns": [T1], "scores": [5, 4], "order": "relevance"}, id="long"
        ),
        pytest.param((), {"turns": [T1], "scores": [5]}, id="no-order-echo"),
        pytest.param(
            (), {"turns": [T1], "scores": [5], "order": "newest"}, id="wrong-echo"
        ),
        pytest.param(
            (), {"turns": [T1, T2], "scores": [1, 2], "order": "relevance"}, id="rising"
        ),
        pytest.param(
            (), {"turns": [T1, T2], "scores": [3, 3], "order": "relevance"}, id="tie"
        ),
        pytest.param(
            (), {"turns": [T1, T1], "scores": [3, 3], "order": "relevance"}, id="twice"
        ),
        pytest.param(
            (), {"turns": [T1], "scores": [-1], "order": "relevance"}, id="negative"
        ),
        pytest.param(
            (), {"turns": [T1], "scores": [1.5], "order": "relevance"}, id="fraction"
        ),
        pytest.param(
            (), {"turns": [T1], "scores": ["5"], "order": "relevance"}, id="text"
        ),
        pytest.param(
            ("--limit", "1"),
            {"turns": [T2, T1], "scores": [5, 4], "order": "relevance"},
            id="over-limit",
        ),
        pytest.param(
            ("--before", "2026-11-02T10:00:00Z"),
            {"turns": [T2], "scores": [5], "order": "relevance"},
            id="at-before",
        ),
        pytest.param(
            ("--since", "2026-11-02T10:00:00Z"),
            {"turns": [T1], "scores": [5], "order": "relevance"},
            id="before-since",
        ),
        pytest.param(
            ("--conversation", "s2"),
            {"turns": [T1], "scores": [5], "order": "relevance"},
            id="unselected",
        ),
        pytest.param(
            BOUNDED_NEWEST,
            {"turns": [T1], "scores": [5], "order": "newest"},
            id="newest-scores",
        ),
        pytest.param(
            BOUNDED_NEWEST,
            {"turns": [T1, T2], "order": "newest"},
            id="newest-oldest-first",
        ),
        pytest.param(
            BOUNDED_NEWEST,
            {"turns": [T2], "order": "relevance"},
            id="newest-wrong-echo",
        ),
        pytest.param(
            (),
            {"turns": [ERASED_TURN], "scores": [5], "order": "relevance"},
            id="redacted",
        ),
    ],
)
def test_malformed_ranked_pages_are_refused(
    binary: Binary, flags: tuple[str, ...], page: Any
) -> None:
    observed: list[dict[str, Any]] = []
    with responder(fake_store(RANKING, page, observed)) as (url, _):
        args = ("search", "needle", "--url", url, *flags, "--json")
        result = run(binary, *args, env=CLIENT)
        assert result.returncode == 1, result.stdout
        assert "malformed wire response" in result.stderr
        assert len(observed) == 1 and observed[0]["order"] in {"relevance", "newest"}


def test_mcp_refuses_a_malformed_ranked_page(binary: Binary) -> None:
    page = {"turns": [T2, T1], "scores": [5], "order": "relevance"}
    with responder(fake_store(RANKING, page, [])) as (url, _):
        _, results = mcp(
            binary,
            [{"query": "needle"}],
            "--url",
            url,
            "--scope",
            "user:test",
            env=CLIENT,
        )
        assert "malformed wire response" in refusal(results[0])


def history(selection: str) -> str:
    return (
        PROFILE
        + '\n[history]\nsource = "knowledge"\nscope = "workspace:portable"\n'
        + selection
    )


def test_profile_history_source_answers_the_ranked_search(binary: Binary) -> None:
    data = binary.directory / "data"
    plant(data, {**CORPUS, **HIDDEN})
    install(binary, history('conversations = ["alpha-session", "beta-session"]\n'))
    bind(binary, "--root", str(data))
    page = search(binary, *QUERY, "--profile", "portable")
    assert page["conversations"] == ["alpha-session", "beta-session"]
    # A leaked hidden turn would add a hit and shift every statistic.
    assert scored(page) == RANKED
    bounded = search(binary, *QUERY, "--profile", "portable", *WINDOW)
    assert scored(bounded) == IN_WINDOW
    answers = rpc(binary, [("search_history", {"query": "Dashboard RESPONSE time"})])
    text = answer(answers[2]["result"])
    assert headers(text) == pairs(RANKED)
    assert text.endswith("\n" + RANKED_FOOTER.format(10))


def test_remote_profile_history_ranks_within_its_grants(binary: Binary) -> None:
    plant(binary.home, CORPUS, prefix=PREFIX)
    plant(binary.home, HIDDEN)
    with serve(binary, extra_env=grant_env(binary.directory / "grants.toml")) as server:
        # The hidden session is listed too; only the profile and grants keep it out.
        for conversation in [*(PREFIX + c for c in CORPUS), *HIDDEN]:
            server.post(
                "memory/write",
                scope="workspace:portable",
                path="sessions/" + conversation,
                content="session pointer",
            )
        install(binary, history(f'prefix = "{PREFIX}"\n'))
        bind(binary, "--url", server.url, "--token-env", "WORKSPACE_TOKEN")
        token = {"WORKSPACE_TOKEN": "reader-token"}
        page = search(binary, *QUERY, "--profile", "portable", env=token)
        assert scored(page) == [(PREFIX + c, t, s) for c, t, s in RANKED]
        bounded = search(binary, *QUERY, "--profile", "portable", *WINDOW, env=token)
        assert scored(bounded) == [(PREFIX + c, t, s) for c, t, s in IN_WINDOW]
        answers = rpc(
            binary,
            [("search_history", {"query": "Dashboard RESPONSE time", **BOUNDS})],
            env=token,
        )
        text = answer(answers[2]["result"])
        assert headers(text) == [(PREFIX + c, t) for c, t in pairs(IN_WINDOW)]
        assert "other-hidden" not in text
