"""What a home can hold transfers faithfully, and intake never changes the
home (the L1b slice of the L1 plan, DESIGN §40.54 items 12, 14 and 22).

A home holds a knowledge collection with its definition and a structured
document in two versions, a skill package whose resource was revised, an
erased document with its erasure act, a document with opaque envelope
keys, a conversation with a turn of 3 MiB, an erased turn, projections and
a trail act, and a conversation holding a projection alone. Its snapshot's
bodies are posted as they are to a server that keeps records, and an export
of that server's store equals an export of the home, file for file, except
for the conversation the snapshot names as omitted.

The home stays byte for byte as it was when it holds a pending memory redo
plan, which the snapshot replays on its copy alone, and when a writer that
takes the store lock appends to a conversation while the snapshot is
taken: the copy holds whole writes only, and the home holds the writer's
bytes and nothing else new.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.knowledge_support import (
    bundle,
    invoke as knowledge,
    marked,
    success,
)
from tests.contract.records.restoring import RESTORE
from tests.contract.records.snapshotting import (
    check_conversation,
    login_snapshot,
    part,
    tree,
)
from tests.contract.records.support import compact, enable
from tests.contract.records.synthetic import message
from tests.contract.skill_support import invoke as skill, package
from tests.contract.support import Binary, executable, serve

pytestmark = pytest.mark.timeout(600)

EXTRAS = "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-01T00:00:00Z'\nupdated_at: '2026-10-01T00:00:00.5Z'\nactor: seed\norigin: früh ✓\nticket: 7\n---\nopaque keys\n"
LARGE, ENTRIES, LIVE = "cc-large", "cc-entries", "cc-live"


def write(binary: Binary, path: str, scope: str, content: str) -> None:
    result = binary.run(
        "memory", "create", path, "--scope", scope, "--content", "-", stdin=content
    )
    assert result.returncode == 0, result.stderr


def rows(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        compact({"neosian_format": 1} | item).decode() + "\n" for item in items
    )
    with path.open("a", encoding="utf-8") as file:
        file.write(text)


def turn(
    number: int, messages: list[dict[str, Any]], redacted: bool = False
) -> dict[str, Any]:
    made: dict[str, Any] = {"turn": number, "messages": messages}
    made["created_at"] = f"2026-10-0{number}T10:00:00.250000Z"
    made |= {"actor": "claude-code:large", "redacted": redacted}
    return made


def build(binary: Binary) -> None:
    rules = binary.directory / "rules.json"
    definition = {"format": 1, "name": "Decisions", "schema": {"fields": {}}}
    rules.write_text(json.dumps(definition), encoding="utf-8")
    flags = ["/memories/notes", "--file", str(rules), "--write"]
    success(knowledge(binary, "collection", *flags))
    first = marked("A decision", references=[{"relation": "supports", "path": "b"}])
    source = bundle(binary.directory / "bundle", first)
    success(
        knowledge(
            binary,
            "import",
            str(source),
            "--format",
            "bundle",
            "--to",
            "/memories/notes",
            "--write",
        )
    )
    write(binary, "/memories/notes/a", "user:knowledge", marked("Revised decision"))
    target = "/memories/skills/review"
    pack = package(binary.directory / "package")
    assert skill(binary, "import", str(pack), "--to", target, "--write").returncode == 0
    resource = f"{target}/references/reason.md"
    write(binary, resource, "user:skills", "# Evidence\r\nRevised.\r\n")
    write(binary, "/memories/secret", "user:knowledge", "first secret")
    write(binary, "/memories/secret", "user:knowledge", "second secret")
    erased = binary.run(
        "memory", "redact", "/memories/secret", "--scope", "user:knowledge"
    )
    assert erased.returncode == 0, erased.stderr
    extras = binary.home / "user%3Aextras"
    (extras / "documents").mkdir(parents=True)
    (extras / "documents" / "a.md").write_text(EXTRAS, encoding="utf-8")
    row = {"path": "a", "version": 1, "action": "created", "content": "opaque keys\n"}
    row |= {"actor": "seed", "created_at": "2026-10-01T00:00:00Z", "redacted": False}
    rows(extras / "versions" / "a.jsonl", [row])
    large = binary.home / "conversations" / LARGE
    output = ("line of tool output ✓ " * 50 + "\n") * 2_800
    call = {"id": "call-1", "name": "Bash", "arguments": {"command": "make test"}}
    rows(
        large / "turns.jsonl",
        [
            turn(
                1,
                [message("user", "run it"), message("assistant", None, [call])]
                + [message("tool", output, call_id="call-1")],
            ),
            turn(2, [], redacted=True),
            turn(3, [message("user", "done")]),
        ],
    )
    entry = {"kind": "epoch", "text": "kept ✓", "span": 1}
    rows(large / "projections.jsonl", [{"turn": 3} | entry, {"turn": 1} | entry])
    act = {"created_at": "2026-10-05T00:00:00Z", "actor": None, "turns": [2]}
    rows(large / "redactions.jsonl", [act, act | {"actor": "cli:erase"}])
    rows(
        binary.home / "conversations" / ENTRIES / "projections.jsonl",
        [{"turn": 1} | entry],
    )


def test_an_export_of_the_restored_units_is_the_export_of_the_home(
    binary: Binary, tmp_path: Path
) -> None:
    build(binary)
    before = tree(binary.home)
    bodies = tmp_path / "bodies"
    answer = login_snapshot(binary, "take", bodies)
    assert tree(binary.home) == before
    names = [(u["kind"], u["name"]) for u in answer["units"]]
    assert names == [
        ("scope", "user:extras"),
        ("scope", "user:knowledge"),
        ("scope", "user:skills"),
        ("conversation", LARGE),
    ]
    assert [o["name"] for o in answer["omitted"]] == [ENTRIES]
    large = answer["units"][3]
    assert large["parts"] >= 4 and (large["turns"], large["redactions"]) == (3, 2)
    check_conversation(binary.home, bodies, 3, large)
    target = Binary(executable(), tmp_path / "to")
    target.directory.mkdir()
    enable(target)
    with serve(target) as server:
        for index, unit in enumerate(answer["units"]):
            if unit["kind"] == "scope":
                raw = part(bodies, index, 0)
                reply = server.request("/v1/store/restore_scope", raw=raw)
                assert reply.status == 200, reply
                continue
            stored, digest = 0, None
            for number in range(unit["parts"]):
                raw = part(bodies, index, number)
                reply = server.request("/v1/" + RESTORE, raw=raw)
                assert reply.status == 200, reply
                stored, digest = (
                    stored + reply.body["stored_bytes"],
                    reply.body["digest"],
                )
            assert (stored, digest) == (unit["bytes"], unit["sha256"])
    home, restored = tmp_path / "home-export", tmp_path / "restored-export"
    assert binary.run("export", str(home), "--json").returncode == 0
    exported = binary.run("export", str(restored), "--root", str(target.home), "--json")
    assert exported.returncode == 0, exported.stderr
    # Each export writes its own change feed (a reset event per restored
    # scope, with a fresh stream and time); that is the archive's, not data.
    expected = {
        path: digest
        for path, digest in tree(home).items()
        if not path.startswith(f"conversations/{ENTRIES}/") and "/.changes/" not in path
    }
    found = {p: d for p, d in tree(restored).items() if "/.changes/" not in p}
    assert found == expected
    resource = home / "user%3Askills/versions/skills/review/references/reason.md.jsonl"
    assert len(resource.read_text("utf-8").splitlines()) == 2
    assert "user%3Aknowledge/redactions.jsonl" in expected


def test_a_pending_redo_plan_is_replayed_in_the_snapshot_alone(
    binary: Binary, tmp_path: Path
) -> None:
    write(binary, "/memories/a", "user:pending", "before")
    document = binary.home / "user%3Apending" / "documents" / "a.md"
    text = document.read_text("utf-8").replace("before", "recovered")
    writes = [{"path": "user%3Apending/documents/a.md", "operation": "put"}]
    writes[0]["text"] = text
    pending = binary.home / ".transactions" / "pending.json"
    pending.parent.mkdir(exist_ok=True)
    pending.write_text(json.dumps({"format": 1, "writes": writes}), encoding="utf-8")
    before = tree(binary.home)
    login_snapshot(binary, "take", tmp_path / "bodies")
    assert tree(binary.home) == before and pending.is_file()
    body = json.loads(part(tmp_path / "bodies", 0, 0))
    assert body["documents"][0]["content"] == "recovered"
    assert login_snapshot(binary, "dispose") == {"disposed": True}
    assert tree(binary.home, area=True) == before


def live_turn(number: int) -> dict[str, Any]:
    made = turn(1, [message("user", f"live prompt {number}")])
    return made | {"turn": number}


@pytest.mark.skipif(os.name != "posix", reason="the writer locks with flock")
def test_a_home_written_during_intake_is_copied_between_whole_writes(
    binary: Binary, tmp_path: Path
) -> None:
    write(binary, "/memories/a", "user:live", "before")
    lock = binary.home / ".store.lock"
    assert lock.is_file()
    directory = binary.home / "conversations" / LIVE
    entry = {"kind": "log", "span": 1}
    rows(directory / "turns.jsonl", [live_turn(1)])
    rows(directory / "projections.jsonl", [{"turn": 1, "text": "p 1"} | entry])
    before = tree(binary.home)
    turns, entries = directory / "turns.jsonl", directory / "projections.jsonl"
    initial = (turns.read_bytes(), entries.read_bytes())
    stop, rounds = threading.Event(), [1]

    def writer() -> None:
        while not stop.is_set():
            number = rounds[0] + 1
            with lock.open("rb") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                rows(turns, [live_turn(number)])
                time.sleep(0.003)
                rows(entries, [{"turn": number, "text": f"p {number}"} | entry])
                fcntl.flock(handle, fcntl.LOCK_UN)
            rounds[0] = number
            time.sleep(0.001)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while rounds[0] < 6:
            time.sleep(0.005)
        answer = login_snapshot(binary, "take", tmp_path / "bodies")
        taken = rounds[0]
        while rounds[0] < taken + 5:
            time.sleep(0.005)
    finally:
        stop.set()
        thread.join(10)
    index, unit = next(
        (i, u) for i, u in enumerate(answer["units"]) if u["name"] == LIVE
    )
    copied = unit["turns"]
    assert unit["projections"] == copied and 6 <= copied < rounds[0]
    cut = tmp_path / "cut" / "conversations" / LIVE
    cut.mkdir(parents=True)
    for name in ("turns.jsonl", "projections.jsonl"):
        lines = (directory / name).read_bytes().splitlines(keepends=True)
        (cut / name).write_bytes(b"".join(lines[:copied]))
    check_conversation(tmp_path / "cut", tmp_path / "bodies", index, unit)
    after = tree(binary.home)
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    assert changed == {
        f"conversations/{LIVE}/turns.jsonl",
        f"conversations/{LIVE}/projections.jsonl",
    }
    written = range(2, rounds[0] + 1)
    expected_turns = initial[0] + b"".join(
        compact({"neosian_format": 1} | live_turn(n)) + b"\n" for n in written
    )
    assert turns.read_bytes() == expected_turns
    expected_entries = initial[1] + b"".join(
        compact({"neosian_format": 1, "turn": n, "text": f"p {n}"} | entry) + b"\n"
        for n in written
    )
    assert entries.read_bytes() == expected_entries
