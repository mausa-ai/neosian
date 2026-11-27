"""A transfer carries a conversation's whole erasure trail in storage order
(the T1e2d slice of the T1e2 plan, DESIGN §40.54 item 22): more than 500 acts,
times that go backwards and a tie across the old 500 boundary round-trip
through `neosian import` and `neosian export` on a FileStore, after acts the
destination already held. A server answers only its newest 500 acts, so an
export from one refuses a full page and uses a shorter answer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.support import ADMIN, Binary, serve

ID = "trail-session"
COUNT = 505
# 1-based storage positions sharing the oldest time: they cover the page
# boundary after the 500th act of the archive (497..503) and of a store that
# holds two earlier acts first (498..499), and the newest 500 by time leave
# five of them out.
TIED = range(497, 504)
TIE = "2026-08-19T09:00:00Z"


def act(time: str, actor: str | None, turns: list[int]) -> dict[str, Any]:
    return {"neosian_format": 1, "created_at": time, "actor": actor, "turns": turns}


def acts(count: int = COUNT) -> list[dict[str, Any]]:
    """Distinct acts whose times jump about; 11 and 12 share a time too."""
    rows: list[dict[str, Any]] = []
    for index in range(count):
        position = index + 1
        second = (index * 37) % 503
        time = f"2026-08-19T10:{second // 60:02}:{second % 60:02}"
        time += ".250000Z" if index % 5 == 0 else "Z"
        if position in TIED:
            time = TIE
        if position == 12:
            time = rows[10]["created_at"]
        actor = None if index % 7 == 3 else f"cli:act-{position:03}"
        turns = [1, 3] if index % 3 == 0 else [1 + index % 4]
        rows.append(act(time, actor, turns))
    return rows


PRIOR = [
    act("2026-08-20T00:00:00Z", "cli:prior-1", [2]),
    act("2026-08-18T00:00:00Z", "cli:prior-2", [4]),
]


def turn(number: int) -> dict[str, Any]:
    message = {
        "role": "user",
        "content": [{"type": "text", "text": f"turn {number}"}],
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }
    return {
        "neosian_format": 1,
        "turn": number,
        "created_at": f"2026-08-19T08:00:0{number}Z",
        "messages": [message],
        "actor": None,
    }


def lines(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)


def archive(root: Path, trail: list[dict[str, Any]], turns: int = 4) -> Path:
    """A FileStore root holding one conversation: its turns and its trail."""
    directory = root / "conversations" / ID
    directory.mkdir(parents=True)
    if turns:
        text = lines([turn(number) for number in range(1, turns + 1)])
        (directory / "turns.jsonl").write_text(text, encoding="utf-8")
    (directory / "redactions.jsonl").write_text(lines(trail), encoding="utf-8")
    return root


def trail_of(root: Path) -> list[dict[str, Any]]:
    text = (root / "conversations" / ID / "redactions.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines()]


def transfer(
    binary: Binary, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), *args, "--json"],
        env=binary.environment() | env,
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def done(result: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    assert result.returncode == 0, result.stdout + result.stderr
    units: list[dict[str, Any]] = json.loads(result.stdout)["units"]
    return units


def same_acts(read: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    """Actors, times and turn lists, act by act, in order."""
    assert len(read) == len(expected)
    for at, (got, want) in enumerate(zip(read, expected, strict=True), start=1):
        assert (got["actor"], got["created_at"], got["turns"]) == (
            want["actor"],
            want["created_at"],
            want["turns"],
        ), f"act {at}"


def test_seed_crosses_the_old_boundary_inside_a_tie() -> None:
    seeded = acts()
    times = sorted(row["created_at"] for row in seeded)
    # The newest 500 by time would cut through the tie.
    assert times[COUNT - 501] == times[COUNT - 500] == TIE
    assert seeded[499]["created_at"] == seeded[500]["created_at"] == TIE
    times_in_order = [row["created_at"] for row in seeded]
    assert times_in_order != sorted(times_in_order), "times go backwards"
    assert len({json.dumps(row) for row in seeded}) == COUNT


def test_filestore_import_and_export_keep_every_act_in_storage_order(
    binary: Binary,
) -> None:
    seeded = acts()
    store = binary.directory / "store"
    prior = archive(binary.directory / "prior", PRIOR, turns=0)
    source = archive(binary.directory / "archive", seeded)
    # A conversation holding only a trail is not listed, so it is named.
    seed = ("import", str(prior), "--root", str(store), "--conversation", ID)
    assert [u["redactions"] for u in done(transfer(binary, *seed))] == [2]
    units = done(transfer(binary, "import", str(source), "--root", str(store)))
    assert [(u["name"], u["turns"], u["redactions"]) for u in units] == [(ID, 4, COUNT)]
    same_acts(trail_of(store), PRIOR + seeded)
    exported = binary.directory / "exported"
    units = done(transfer(binary, "export", str(exported), "--root", str(store)))
    assert [(u["name"], u["turns"], u["redactions"]) for u in units] == [
        (ID, 4, COUNT + 2)
    ]
    same_acts(trail_of(exported), PRIOR + seeded)
    assert (exported / "conversations" / ID / "turns.jsonl").read_text("utf-8") == (
        source / "conversations" / ID / "turns.jsonl"
    ).read_text("utf-8")


def test_server_export_refuses_a_full_page_and_uses_a_shorter_one(
    binary: Binary,
) -> None:
    for count in (500, 499):
        seeded = acts(count)
        shutil.rmtree(binary.home, ignore_errors=True)
        archive(binary.home, seeded)
        exported = binary.directory / f"exported-{count}"
        with serve(binary) as server:
            result = transfer(
                binary,
                "export",
                str(exported),
                "--url",
                server.url,
                "--conversation",
                ID,
                NEOSIAN_CLIENT_TOKEN=ADMIN,
            )
        if count == 500:
            assert result.returncode == 1
            message = json.loads(result.stdout)["error"]
            assert "at least 500 erasure acts" in message
            assert "--root" in message and "NEOSIAN_POSTGRES_DSN" in message
            assert not (exported / "conversations" / ID).exists()
            continue
        assert [(u["turns"], u["redactions"]) for u in done(result)] == [(4, 499)]
        # The audit read orders by time, then by storage among equal times.
        order = sorted(range(count), key=lambda at: seeded[at]["created_at"])
        same_acts(trail_of(exported), [seeded[at] for at in order])
