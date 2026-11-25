"""Item 9's example turn (DESIGN §40.54 items 3.8 and 9), written by hand:
a prompt, one `Bash` call, a tool result of 812,345 bytes in thirteen chunks
of at most 65,000 bytes, and a reply."""

from __future__ import annotations

from typing import Any

from tests.contract.records.support import record

SESSION = "cc-7f3a"
DRAFT = "d7c1e0b2a9f44e57"
RESULT = ("a" * 999 + "\n") * 812 + "a" * 345
CHUNK = 65_000
CHUNKS = [RESULT[start : start + CHUNK] for start in range(0, len(RESULT), CHUNK)]
EXAMPLE = [
    record(0, 0, "user", "prompt", "run the tests"),
    record(
        1,
        1,
        "assistant",
        "tool_call",
        '{"command":"make test"}',
        content="none",
        call=0,
        call_id="toolu_01",
        name="Bash",
    ),
    *[
        record(
            2 + index,
            2,
            "tool",
            "tool_result",
            text,
            content="text" if index == 0 else None,
            call_id="toolu_01",
            chunk=index,
            size=812_345,
        )
        for index, text in enumerate(CHUNKS)
    ],
    record(15, 3, "assistant", "reply", "All 412 tests pass."),
]
MESSAGES: list[dict[str, Any]] = [
    {
        "role": "user",
        "content": "run the tests",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
    {
        "role": "assistant",
        "content": None,
        "reasoning": None,
        "tool_calls": [
            {"id": "toolu_01", "name": "Bash", "arguments": {"command": "make test"}}
        ],
        "tool_call_id": None,
    },
    {
        "role": "tool",
        "content": RESULT,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": "toolu_01",
    },
    {
        "role": "assistant",
        "content": "All 412 tests pass.",
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    },
]

# The same turn with a result of 130-byte lines, for item 10's second
# example. The split rule's longest prefix holds 500 whole lines and part of
# the next, and is cut back after the last line feed: thirteen chunks of at
# most 65,000 bytes again, each holding 500 line feeds that the wire escapes.
LINED = ("a" * 129 + "\n") * 6_248 + "a" * 105
LINED_CHUNKS = [LINED[start : start + CHUNK] for start in range(0, len(LINED), CHUNK)]
LINED_EXAMPLE = [
    EXAMPLE[0],
    EXAMPLE[1],
    *[
        {**item, "text": text}
        for item, text in zip(EXAMPLE[2:15], LINED_CHUNKS, strict=True)
    ],
    EXAMPLE[15],
]
LINED_MESSAGES = [
    MESSAGES[0],
    MESSAGES[1],
    {**MESSAGES[2], "content": LINED},
    MESSAGES[3],
]

# Item 11's example: the same turn with one failing assertion in its result.
# The sentence lies in the eighth chunk, record 9, with "failed assertion"
# starting at scalar 31,547 of that record, so the excerpt starts 40 before,
# at 31,507 as the design shows. One word is laid across the cut between
# records 2 and 3, where a record search does not find it.
SENTENCE = "thread 'cache::ttl' panicked: failed assertion: left == right"
CUT_WORD = "splitx"


def _laid(text: str, at: int, word: str) -> str:
    return text[:at] + word + text[at + len(word) :]


FAILED = _laid(_laid(RESULT, 7 * CHUNK + 31_517, SENTENCE), CHUNK - 3, CUT_WORD)
FAILED_CHUNKS = [
    FAILED[start : start + CHUNK] for start in range(0, len(FAILED), CHUNK)
]
FAILED_EXAMPLE = [
    EXAMPLE[0],
    EXAMPLE[1],
    *[
        {**item, "text": text}
        for item, text in zip(EXAMPLE[2:15], FAILED_CHUNKS, strict=True)
    ],
    EXAMPLE[15],
]
