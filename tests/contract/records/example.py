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
