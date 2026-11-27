"""The listing's `recorded` and `size` lines and the left-off block on
Postgres (DESIGN §40.54 item 19, #448; the T1fd slice). Two turns land
through the real hooks and a foreground `neosian record-land` into a schema
with records extension 1 and into a twin schema without it. The records
schema's listing carries the cumulative kinds and the turns' UTF-8 bytes,
computed here from what the hooks sent; the twin's carries neither. The
session start shows the same "where we left off" block from both. The
FileStore twins are in `tests/contract/landing/test_listing.py` and
`tests/contract/records/test_left_off.py`."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from tests.contract.postgres.test_landing_records import (
    Database,
    dsn,
    hook,
    install_records,
    land,
    plain_schema,
)
from tests.contract.records.test_left_off import CLOSE, OPEN
from tests.contract.support import Binary

pytestmark = pytest.mark.postgres

SESSION = "records-session"
SCOPE = "user:contract"
OUTPUT = 'é 中 😀 "quoted"\n' * 40


def exchange(binary: Binary, schema: str, prompt: str, tools: int) -> None:
    hook(binary, schema, "UserPromptSubmit", prompt=prompt)
    for n in range(tools):
        hook(
            binary,
            schema,
            "PostToolUse",
            tool_name="Bash",
            tool_use_id=f"toolu_{n}",
            tool_input={"command": f"cat é-{n}.txt"},
            tool_response=OUTPUT,
        )
    hook(binary, schema, "Stop", last_assistant_message=f"{prompt}: done ✓")


def listing(binary: Binary, schema: str) -> str:
    """The sessions document as `memory view` prints it, unnumbered."""
    viewed = subprocess.run(
        [str(binary.executable), "memory", "view", f"/memories/sessions/{SESSION}"]
        + ["--scope", SCOPE, "--schema", schema],
        env={**binary.environment(), "NEOSIAN_POSTGRES_DSN": dsn()},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert viewed.returncode == 0, viewed.stderr
    numbered = viewed.stdout.split("\n")[1:-1]
    return "\n".join(line.split(": ", 1)[1] for line in numbered)


def left_off(answer: Any) -> str:
    context = str(answer["context"])
    return OPEN + context.split(OPEN, 1)[1].split(CLOSE, 1)[0] + CLOSE


def test_postgres_records_list_the_pair_and_start_from_light_records(
    database: Database, binary: Binary
) -> None:
    connection, name, _ = database
    install_records(connection, binary, name)
    with plain_schema(connection, binary) as twin:
        for prompt, tools in (("first é", 1), ("second", 0)):
            for schema in (name, twin):
                exchange(binary, schema, prompt, tools)
                assert land(binary)["landed"] == 1
        size = len("first é".encode()) + len(OUTPUT.encode())
        size += len('{"command":"cat é-0.txt"}'.encode())
        size += len("first é: done ✓".encode()) + len(b"second")
        size += len("second: done ✓".encode())
        pair = (
            f"- recorded: prompt, reply, tool_call, tool_result\n- size: {size} bytes\n"
        )
        assert listing(binary, name).endswith(f"- turns: 2\n{pair}")
        plain = listing(binary, twin)
        assert plain.endswith("- turns: 2\n") and "- size:" not in plain, plain
        shown = [
            left_off(hook(binary, schema, "SessionStart", source="startup"))
            for schema in (name, twin)
        ]
        assert shown[0] == shown[1]
        assert f"\n[conversation {SESSION} — written by claude-code:{SESSION}]\n" in (
            shown[0]
        )
        assert "\n[2] USER: second | AGENT: second: done ✓\n" in shown[0], shown[0]
        assert json.dumps(shown[0]).count("TOOL Bash") == 1
