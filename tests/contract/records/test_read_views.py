"""`conversation/read_records`, its views (DESIGN §40.54 items 3 and 10):
the kind selections of the brief, heads with `tail`, `size` and `truncated`,
a piece fetched whole, a format-1 turn converted as it is read, and what a
read never shows: an open draft, a staged restore, a redacted turn's
records. One conversation holds a format-1 turn, the same turn as records,
a turn with two long pieces, a redacted turn and a last short one."""

from __future__ import annotations

from typing import Any

import pytest

from tests.contract.records.example import EXAMPLE, MESSAGES
from tests.contract.records.reading import (
    EVERY,
    EVERY_BYTES,
    EVERY_MESSAGES,
    KINDS,
    all_answered,
    answered,
    encoding,
    head_of,
    header,
    message,
    post,
    raw,
    read,
    refusal,
    tail_of,
)
from tests.contract.records.support import record, write
from tests.contract.support import Binary, Server, serve

SESSION = "s-views"
DIGITS = "0123456789" * 12
ESCAPED = 'é"\n' * 30
LONG = [
    record(0, 0, "user", "prompt", "run"),
    record(
        1,
        1,
        "assistant",
        "tool_call",
        '{"command":"make"}',
        content="none",
        call=0,
        call_id="t1",
        name="Bash",
    ),
    record(2, 2, "tool", "tool_result", DIGITS[:50], call_id="t1", size=120),
    record(
        3,
        2,
        "tool",
        "tool_result",
        DIGITS[50:100],
        content=None,
        call_id="t1",
        chunk=1,
        size=120,
    ),
    record(
        4,
        2,
        "tool",
        "tool_result",
        DIGITS[100:],
        content=None,
        call_id="t1",
        chunk=2,
        size=120,
    ),
    record(5, 3, "assistant", "reply", "done"),
    record(6, 4, "user", "prompt", ESCAPED[:45], size=120),
    record(7, 4, "user", "prompt", ESCAPED[45:], content=None, chunk=1, size=120),
]
LONG_BYTES = 3 + 18 + 120 + 4 + 120
LAST = [record(0, 0, "user", "prompt", "bye")]
TEXT: dict[str, Any] = {"reasoning": None, "tool_calls": [], "tool_call_id": None}


def conversation(server: Server) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The five turns; their headers and every record a full read answers."""
    assert len(ESCAPED.encode()) == 120 and len(DIGITS) == 120
    server.post(
        "conversation/append_turn", conversation_id=SESSION, messages=EVERY_MESSAGES
    )
    write(server, SESSION, "every", EVERY, seal=True, actor="me")
    write(server, SESSION, "long", LONG, seal=True)
    gone = [{"role": "user", "content": "forget this", **TEXT}]
    server.post("conversation/append_turn", conversation_id=SESSION, messages=gone)
    erased = server.post(
        "conversation/redact_turns", conversation_id=SESSION, turns=[4]
    )
    assert erased == {"count": 1}
    # An open draft with records: never part of any read.
    opened = write(server, SESSION, "open", LONG[:3])
    assert (opened["state"], opened["next_seq"]) == ("open", 3)
    bye = [{"role": "user", "content": "bye", **TEXT}]
    server.post("conversation/append_turn", conversation_id=SESSION, messages=bye)
    stored = server.post("conversation/read_turns", conversation_id=SESSION)["turns"]
    assert [turn["actor"] for turn in stored] == [
        "client:contract",
        "client:contract/me",
        "client:contract",
        "client:contract",
        "client:contract",
    ]
    assert [turn["redacted"] for turn in stored] == [False, False, False, True, False]
    headers = [
        header(stored[0], 9, EVERY_BYTES),
        header(stored[1], 9, EVERY_BYTES),
        header(stored[2], 8, LONG_BYTES),
        header(stored[3], 0, 0, redacted=True),
        header(stored[4], 1, 3),
    ]
    items = (
        all_answered(1, EVERY)
        + all_answered(2, EVERY)
        + all_answered(3, LONG)
        + all_answered(5, LAST)
    )
    return headers, items


def of(items: list[dict[str, Any]], *kinds: str) -> list[dict[str, Any]]:
    return [item for item in items if item["kind"] in kinds]


def test_the_views_of_the_brief_are_kind_selections(records: Server) -> None:
    """Every header is answered whatever is selected: a turn the page
    passes with no record selected is still a turn it reaches."""
    headers, items = conversation(records)

    def view(*kinds: str) -> dict[str, Any]:
        return {"turns": headers, "records": of(items, *kinds), "continuation": None}

    everything = {"turns": headers, "records": items, "continuation": None}
    assert read(records, SESSION) == everything
    assert len(items) == 27 and {item["kind"] for item in items} == set(KINDS)
    assert read(records, SESSION, kinds=KINDS) == everything
    # Prompts and replies; prompts alone; tool calls without results, alone
    # and with prompt and reply for context.
    chat = read(records, SESSION, kinds=["prompt", "reply"])
    assert chat == view("prompt", "reply")
    assert [(item["turn"], item["seq"]) for item in chat["records"]] == [
        (1, 1),
        (1, 3),
        (2, 1),
        (2, 3),
        (3, 0),
        (3, 5),
        (3, 6),
        (3, 7),
        (5, 0),
    ]
    assert read(records, SESSION, kinds=["prompt"]) == view("prompt")
    calls = read(records, SESSION, kinds=["tool_call"])
    assert calls == view("tool_call")
    assert [item["text"] for item in calls["records"]] == [
        '{"command":"ls"}',
        '{"command":"ls"}',
        '{"command":"make"}',
    ]
    context = ["tool_call", "prompt", "reply"]
    assert read(records, SESSION, kinds=context) == view(*context)
    # A head longer than the pieces changes nothing: `tail` stays null.
    light = read(records, SESSION, kinds=context, heads={"tool_call": 2_048})
    assert light == view(*context)
    full = read(records, SESSION, heads={"tool_result": 4_096})
    assert full == everything
    # Order and repeats in `kinds` are free; the answer keeps (turn, seq).
    assert read(records, SESSION, kinds=["reply", "prompt", "reply"]) == chat


def test_kinds_is_a_set_however_often_a_kind_is_named(records: Server) -> None:
    """Ruling R7: a list of 400,000 entries answers the bytes of the two
    kinds it names, and an unknown kind at its end is still refused."""
    conversation(records)
    body: dict[str, Any] = {"conversation_id": SESSION}
    plain = raw(records, {**body, "kinds": ["extra", "reply"]})
    repeated = ["extra"] * 200_000 + ["reply"] * 199_999 + ["extra"]
    assert raw(records, {**body, "kinds": repeated}) == plain
    assert plain[0] == 200 and plain[1].count(b'"kind":"reply"') == 3
    absent = raw(records, {**body, "kinds": ["attachment"] * 300_000, "turn": 5})
    assert absent == raw(records, {**body, "kinds": ["attachment"], "turn": 5})
    late = message(records, {**body, "kinds": [*repeated, "replies"]})
    assert late == 'unknown record kind "replies"'
    # A continuation binds the set: issued for one spelling, good for another.
    first = read(records, SESSION, kinds=["extra", "reply"], page_bytes=1)
    again = raw(
        records, {**body, "kinds": repeated, "continuation": first["continuation"]}
    )
    assert again == raw(
        records,
        {**body, "kinds": ["reply", "extra"], "continuation": first["continuation"]},
    )
    assert again[0] == 200


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_alone_excludes_the_other_seven(records: Server, kind: str) -> None:
    headers, items = conversation(records)
    alone = read(records, SESSION, kinds=[kind])
    assert alone == {"turns": headers, "records": of(items, kind), "continuation": None}
    assert alone["records"], "the conversation holds every kind"
    rest = [other for other in KINDS if other != kind]
    without = read(records, SESSION, kinds=rest)
    assert without["records"] == of(items, *rest)
    assert len(alone["records"]) + len(without["records"]) == len(items)


def test_headers_only_reads_no_record_file(enabled: Binary) -> None:
    """Ruling R9: `kinds: []` answers from the turn log alone. Shown by
    removing a sealed turn's records file: its header is still answered,
    and any other view refuses the turn."""
    with serve(enabled) as server:
        headers, _ = conversation(server)
        (enabled.home / "conversations" / SESSION / "records" / "3.jsonl").unlink()
        only = {"turns": headers, "records": [], "continuation": None}
        assert read(server, SESSION, kinds=[]) == only
        assert read(server, SESSION, kinds=[], turn=3) == {
            **only,
            "turns": headers[2:3],
        }
        assert post(server, {"conversation_id": SESSION, "turn": 3}).status != 200
        assert (
            post(server, {"conversation_id": SESSION, "kinds": ["prompt"]}).status
            != 200
        )
        assert read(server, SESSION, through=2)["turns"] == headers[:2]


def test_no_kind_answers_headers_only(records: Server) -> None:
    headers, _ = conversation(records)
    only = {"turns": headers, "records": [], "continuation": None}
    assert read(records, SESSION, kinds=[]) == only
    assert read(records, SESSION, kinds=[], heads={"tool_result": 1}) == only
    assert read(records, SESSION, kinds=[], turn=3) == {**only, "turns": headers[2:3]}
    assert read(records, SESSION, kinds=[], after=3) == {**only, "turns": headers[3:]}
    assert read(records, SESSION, kinds=[], through=2) == {**only, "turns": headers[:2]}


def test_a_format_1_turn_is_converted_as_it_is_read(records: Server) -> None:
    """Items 3.3, 3.6 and 10: the same messages stored whole and stored as
    records answer the same records and the same header counts."""
    headers, _ = conversation(records)
    whole = read(records, SESSION, turn=1)
    sealed = read(records, SESSION, turn=2)
    assert whole == {
        "turns": [headers[0]],
        "records": all_answered(1, EVERY),
        "continuation": None,
    }
    assert sealed["records"] == all_answered(2, EVERY)
    assert (headers[0]["records"], headers[0]["bytes"]) == (9, 136)
    # Item 3.8's turn appended whole: its 812,345-byte result is cut by the
    # split rule after the last line feed of each longest prefix, which is
    # every 65,000 bytes, into thirteen chunks.
    appended = records.post(
        "conversation/append_turn", conversation_id="s-whole", messages=MESSAGES
    )
    converted = read(records, "s-whole", turn=1)
    assert converted["turns"] == [header(appended["turn"], 16, 812_400)]
    assert converted["records"] == all_answered(1, EXAMPLE)
    assert converted["continuation"] is None


def test_a_head_answers_a_long_piece_as_one_record(records: Server) -> None:
    """A piece whose text encodes to more than twice the bytes asked is one
    record: `chunk` 0, the longest prefix and suffix within the bytes,
    quotes included, `size` the whole piece, `truncated` true."""
    headers, items = conversation(records)
    third = all_answered(3, LONG)
    # 120 digits encode to 122 bytes: over twice 16, so a head of 14 bytes
    # of text within its quotes, and the last 14.
    head = answered(
        3, LONG[2], text="01234567890123", tail="67890123456789", truncated=True
    )
    assert (head["seq"], head["chunk"], head["size"]) == (2, 0, 120)
    cut = read(records, SESSION, turn=3, heads={"tool_result": 16})
    assert cut == {
        "turns": [headers[2]],
        "records": [*third[:2], head, *third[5:]],
        "continuation": None,
    }
    # Exactly twice the bytes is not more than twice: the piece is whole.
    assert encoding(DIGITS) == 122
    whole = read(records, SESSION, turn=3, heads={"tool_result": 61})
    assert whole["records"] == third
    most = read(records, SESSION, turn=3, heads={"tool_result": 60})
    assert most["records"][2] == answered(
        3, LONG[2], text=DIGITS[:58], tail=DIGITS[-58:], truncated=True
    )
    # The bound counts the encoding: é is two bytes, a quote and a line
    # feed two each once escaped. Ten bytes hold eight between the quotes.
    assert encoding(ESCAPED) == 30 * 6 + 2
    escaped = read(records, SESSION, turn=3, heads={"prompt": 10})
    assert escaped["records"] == [
        third[0],
        *third[1:6],
        answered(3, LONG[6], text='é"\né', tail='\né"\n', truncated=True),
    ]
    assert (head_of(ESCAPED, 10), tail_of(ESCAPED, 10)) == ('é"\né', '\né"\n')
    # A short piece of a kind named in `heads` is whole, `tail` null.
    assert escaped["records"][0] == answered(3, LONG[0])
    # One byte holds nothing within its quotes; neither do two.
    for size in [1, 2]:
        empty = read(records, SESSION, turn=3, heads={"tool_result": size})
        assert empty["records"][2] == answered(
            3, LONG[2], text="", tail="", truncated=True
        )
    three = read(records, SESSION, turn=3, heads={"tool_result": 3})
    assert three["records"][2] == answered(
        3, LONG[2], text="0", tail="9", truncated=True
    )
    # Several kinds at once, each with its own bytes; over the conversation.
    both = read(records, SESSION, heads={"tool_result": 16, "prompt": 10})
    expected = [
        item for item in items if not (item["turn"] == 3 and item["seq"] in (3, 4, 7))
    ]
    expected[20] = head
    expected[22] = escaped["records"][6]
    assert both == {"turns": headers, "records": expected, "continuation": None}
    # A head of a kind the view leaves out answers nothing of it.
    hidden = read(records, SESSION, turn=3, kinds=["reply"], heads={"tool_result": 16})
    assert hidden["records"] == [third[5]]


def test_a_head_applies_to_a_piece_the_read_starts_at_or_before(
    records: Server,
) -> None:
    """A read starting inside a piece answers its remaining chunks as they
    are; one starting at the piece's first chunk answers the head."""
    conversation(records)
    third = all_answered(3, LONG)
    heads = {"tool_result": 16, "prompt": 10}
    inside = read(records, SESSION, turn=3, seq=3, heads=heads)
    escaped = answered(3, LONG[6], text='é"\né', tail='\né"\n', truncated=True)
    assert inside["records"] == [third[3], third[4], third[5], escaped]
    at = read(records, SESSION, turn=3, seq=2, heads=heads)
    assert at["records"][0] == answered(
        3, LONG[2], text="01234567890123", tail="67890123456789", truncated=True
    )
    assert [item["seq"] for item in at["records"]] == [2, 5, 6]


def test_a_piece_is_fetched_whole_from_any_of_its_chunks(records: Server) -> None:
    """`turn`, the `seq` of any chunk and `piece: true`: that piece's
    chunks and nothing else, whatever `heads` says, paged like any read."""
    headers, _ = conversation(records)
    third = all_answered(3, LONG)
    piece = {"turns": [headers[2]], "records": third[2:5], "continuation": None}
    for seq in [2, 3, 4]:
        assert read(records, SESSION, turn=3, seq=seq, piece=True) == piece
    with_heads = read(
        records, SESSION, turn=3, seq=3, piece=True, heads={"tool_result": 16}
    )
    assert with_heads == piece
    assert read(records, SESSION, turn=3, seq=7, piece=True)["records"] == third[6:]
    assert read(records, SESSION, turn=3, seq=0, piece=True)["records"] == third[:1]
    assert read(records, SESSION, turn=3, seq=5, piece=True)["records"] == third[5:6]
    # The converted turn's pieces are fetched alike.
    first = all_answered(1, EVERY)
    assert read(records, SESSION, turn=1, seq=2, piece=True)["records"] == first[2:3]
    # Paged: one chunk a page, the continuation resuming inside the piece.
    seen: list[Any] = []
    continuation = None
    for _ in range(3):
        more: dict[str, Any] = (
            {} if continuation is None else {"continuation": continuation}
        )
        page = read(records, SESSION, turn=3, seq=4, piece=True, page_bytes=1, **more)
        assert page["turns"] == [headers[2]] and len(page["records"]) == 1
        seen += page["records"]
        continuation = page["continuation"]
    assert seen == third[2:5] and continuation is None
    # A record past the turn's last, and a turn that is not there.
    beyond = read(records, SESSION, turn=3, seq=8, piece=True)
    assert beyond == {"turns": [headers[2]], "records": [], "continuation": None}
    absent = read(records, SESSION, turn=9, seq=0, piece=True)
    assert absent == {"turns": [], "records": [], "continuation": None}
    # The view still applies: a piece of a kind left out answers no record.
    left_out = read(records, SESSION, turn=3, seq=3, piece=True, kinds=["prompt"])
    assert left_out == {"turns": [headers[2]], "records": [], "continuation": None}


def test_a_redacted_turn_answers_its_header_alone(records: Server) -> None:
    """Item 3.5: it keeps number, time and actor and has no records."""
    headers, _ = conversation(records)
    gone = read(records, SESSION, turn=4)
    assert gone == {"turns": [headers[3]], "records": [], "continuation": None}
    assert gone["turns"][0] == {
        "turn": 4,
        "created_at": headers[3]["created_at"],
        "actor": "client:contract",
        "redacted": True,
        "records": 0,
        "bytes": 0,
    }
    assert read(records, SESSION, turn=4, seq=0, piece=True) == gone
    # Erasing a records turn removes its records from every view.
    erased = records.post(
        "conversation/redact_turns", conversation_id=SESSION, turns=[3]
    )
    assert erased == {"count": 1}
    after = read(records, SESSION, turn=3)
    assert after["records"] == [] and after["turns"] == [
        {**headers[2], "redacted": True, "records": 0, "bytes": 0}
    ]
    everything = read(records, SESSION)
    assert [item["turn"] for item in everything["records"]] == [1] * 9 + [2] * 9 + [5]
    assert [turn["redacted"] for turn in everything["turns"]] == [
        False,
        False,
        True,
        True,
        False,
    ]


def test_an_open_draft_and_a_staged_restore_are_in_no_read(enabled: Binary) -> None:
    """Items 9 and 12: nothing of a draft or of a restore under way is
    visible. The restore is planted as its bookkeeping alone, since no
    route stages one yet."""
    staged = enabled.home / ".records" / "restore" / "s-staged" / "backup"
    staged.mkdir(parents=True)
    (staged / "state").write_bytes(b"{")
    with serve(enabled) as server:
        nothing: dict[str, Any] = {"turns": [], "records": [], "continuation": None}
        assert read(server, "s-staged") == nothing
        assert read(server, "s-staged", kinds=[]) == nothing
        opened = write(server, "s-draft", "d", LONG[:5])
        assert opened["state"] == "open"
        assert read(server, "s-draft") == nothing
        assert read(server, "s-draft", turn=1) == nothing
        assert read(server, "s-draft", turn=1, seq=2, piece=True) == nothing
        headers, items = conversation(server)
        seen = read(server, SESSION)
        assert seen["turns"] == headers and seen["records"] == items
        # Sealed, the draft is the next turn, whole.
        sealed = write(server, "s-draft", "d", LONG[5:], seal=True)
        assert read(server, "s-draft") == {
            "turns": [sealed["turn"]],
            "records": all_answered(1, LONG),
            "continuation": None,
        }
        assert sealed["turn"]["records"] == 8 and sealed["turn"]["bytes"] == LONG_BYTES


def test_a_converted_turn_with_an_id_past_1024_bytes_is_refused(
    records: Server,
) -> None:
    """Item 10: a format-1 turn whose call id or tool name exceeds 1,024
    bytes cannot be records; the refusal names the bound and `read_turns`
    still answers the turn whole."""
    for session, call, what in [
        ("s-long-id", {"id": "i" * 1_025, "name": "Bash", "arguments": {}}, "call id"),
        ("s-long-name", {"id": "c1", "name": "é" * 513, "arguments": {}}, "tool name"),
    ]:
        said = {"role": "user", "content": "before", **TEXT}
        records.post(
            "conversation/append_turn", conversation_id=session, messages=[said]
        )
        calling = {**TEXT, "role": "assistant", "content": None, "tool_calls": [call]}
        records.post(
            "conversation/append_turn", conversation_id=session, messages=[calling]
        )
        for body in [
            {"conversation_id": session},
            {"conversation_id": session, "turn": 2},
            {"conversation_id": session, "after": 1, "kinds": ["prompt"]},
        ]:
            error = refusal(records, body, 400, "agent_conversation_format_unsupported")
            reason = (
                f"turn 2: message 0 has a {what} longer than 1024 bytes,"
                " which records cannot carry"
            )
            assert error == {
                "code": "agent_conversation_format_unsupported",
                "message": f"Unsupported turn format in conversation '{session}':"
                f" {reason}",
                "details": {"conversation_id": session, "reason": reason},
            }
        # The turn before it reads, and so does a view of headers only up to it.
        before = read(records, session, through=1)
        assert [item["text"] for item in before["records"]] == ["before"]
        whole = records.post("conversation/read_turns", conversation_id=session)
        assert whole["turns"][1]["messages"][0]["tool_calls"][0]["id"] == call["id"]
    at_bound = {"id": "i" * 1_024, "name": "é" * 512, "arguments": {}}
    fits = {**TEXT, "role": "assistant", "content": None, "tool_calls": [at_bound]}
    records.post("conversation/append_turn", conversation_id="s-fits", messages=[fits])
    exact = read(records, "s-fits")["records"]
    assert [(item["call_id"], item["name"]) for item in exact] == [
        ("i" * 1_024, "é" * 512)
    ]
    assert message(records, {"conversation_id": "s-fits", "turn": 0}) == (
        "turn and through are at least 1"
    )
