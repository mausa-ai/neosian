"""A snapshot of the synthetic home yields the units an independent reading
gives (the L1b slice of the L1 plan, DESIGN §40.54 items 12, 14 and 22).

The one-tenth home of `synthetic.py` is the person's home, with a login
credential, a configuration holding a token and a conversation directory
that holds an erasure trail alone. `neosian login-snapshot take` copies it
and measures it. Then: every scope's body and every conversation's parts
are what `snapshotting.py` computes from the home's files; the two
conversations without turns are named as omissions; `neosian import` of
the snapshot sends exactly those bodies, byte for byte, and a server that
keeps records answers each restore with the unit digest and stored bytes
that add up to the unit's length; a second measure of the same snapshot
repeats every byte; the home is unchanged and no file of the snapshot holds
the credential; disposal leaves the home as it was.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.stub import forwarding
from tests.contract.records.differential import run_import
from tests.contract.records.restoring import RESTORE
from tests.contract.records.snapshotting import (
    AREA,
    check_conversation,
    check_scope,
    login_snapshot,
    part,
    tree,
)
from tests.contract.records.support import enable
from tests.contract.records.synthetic import write_home
from tests.contract.support import Binary, executable, serve

pytestmark = pytest.mark.timeout(2_400)

TOKEN = "nsk-login-credential-5f0c1d2e3a4b"
TRAIL_ONLY = "cc-trail-only"


@dataclass
class Taken:
    binary: Binary
    answer: dict[str, Any]
    bodies: Path
    before: dict[str, str]


@pytest.fixture(scope="module")
def taken(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Taken]:
    base = tmp_path_factory.mktemp("login-snapshot")
    binary = Binary(executable(), base)
    write_home(binary.home, "tenth")
    trail = binary.home / "conversations" / TRAIL_ONLY
    trail.mkdir()
    act = {"neosian_format": 1, "created_at": "2026-09-03T00:00:00Z"}
    act |= {"actor": "cli:erase", "turns": [1, 2]}
    (trail / "redactions.jsonl").write_text(json.dumps(act) + "\n", encoding="utf-8")
    login = binary.home / "login"
    login.mkdir(mode=0o700)
    (login / "credential.toml").write_text(f'token = "{TOKEN}"\n', encoding="utf-8")
    (binary.home / "config.toml").write_text(f"# bearer {TOKEN}\n", "utf-8")
    before = tree(binary.home)
    answer = login_snapshot(binary, "take", base / "bodies")
    yield Taken(binary, answer, base / "bodies", before)


def test_every_unit_is_the_one_the_home_files_give(taken: Taken) -> None:
    home, answer = taken.binary.home, taken.answer
    made = answer["units"]
    kinds = [unit["kind"] for unit in made]
    assert kinds == sorted(kinds, key=lambda kind: kind != "scope")
    names = [u["name"] for u in made if u["kind"] == "scope"]
    assert names == sorted(names) and len(names) == 3
    talks = [u["name"] for u in made if u["kind"] == "conversation"]
    directory = home / "conversations"
    held = [p.name for p in directory.iterdir() if (p / "turns.jsonl").exists()]
    assert talks == sorted(held)
    for index, unit in enumerate(made):
        if unit["kind"] == "scope":
            check_scope(home, unit, part(taken.bodies, index, 0))
        else:
            check_conversation(home, taken.bodies, index, unit)
    assert answer["omitted"] == [
        {"kind": "conversation", "name": TRAIL_ONLY, "reason": "no_turns"}
        | {"projections": 0, "redactions": 1},
        {"kind": "conversation", "name": "cc-turnless", "reason": "no_turns"}
        | {"projections": 1, "redactions": 1},
    ]
    totals = {"scopes": 3, "conversations": len(talks)}
    totals |= {"bytes": sum(unit["bytes"] for unit in made)}
    assert answer["totals"] == totals
    snapshot = answer["snapshot"]
    assert Path(snapshot["path"]).resolve() == (home / AREA / "snapshot").resolve()
    assert snapshot["expired"] is False


def test_the_import_sends_these_bodies_and_a_server_answers_them(
    taken: Taken, tmp_path: Path
) -> None:
    target, importer = (Binary(executable(), tmp_path / n) for n in ("to", "by"))
    for binary in (target, importer):
        binary.directory.mkdir()
    enable(target)
    store = taken.binary.home / AREA / "snapshot" / "store"
    with serve(target) as server, forwarding(server.url) as stub:
        reported = run_import(importer, store, stub.url).units
    # The conversation with projections alone goes whole on this route.
    assert len(reported) == len(taken.answer["units"]) + 1
    scoped = [e.raw for e in stub.log if e.route == "store/restore_scope"]
    restores = [e for e in stub.log if e.route == RESTORE]
    expected_scopes = []
    for index, unit in enumerate(taken.answer["units"]):
        if unit["kind"] == "scope":
            expected_scopes.append(part(taken.bodies, index, 0))
            continue
        name = unit["name"]
        sent = [
            e for e in restores if e.body["conversation_id"] == name and len(e.body) > 2
        ]
        assert [e.raw for e in sent] == [
            part(taken.bodies, index, number) for number in range(unit["parts"])
        ], name
        assert all(e.status == 200 for e in sent), name
        stored = sum(e.answer["stored_bytes"] for e in sent)
        assert (stored, sent[-1].answer["digest"]) == (unit["bytes"], unit["sha256"])
    assert scoped == expected_scopes


def test_a_second_measure_repeats_every_byte_and_the_home_is_unchanged(
    taken: Taken,
) -> None:
    again = login_snapshot(taken.binary, "units", taken.bodies.with_name("again"))
    for key in ("units", "omitted", "totals"):
        assert again[key] == taken.answer[key], key
    assert again["snapshot"]["id"] == taken.answer["snapshot"]["id"]
    first = sorted(taken.bodies.iterdir())
    second = sorted(taken.bodies.with_name("again").iterdir())
    assert [p.name for p in first] == [p.name for p in second]
    assert all(
        a.read_bytes() == b.read_bytes() for a, b in zip(first, second, strict=True)
    )
    home = taken.binary.home
    assert tree(home) == taken.before
    assert not (home / ".store.lock").exists()
    area = home / AREA
    assert not (area / "snapshot" / "store" / "login").exists()
    for path in area.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path
        assert path.stat().st_mode & 0o077 == 0, path


def test_disposal_leaves_the_home_as_it_was(taken: Taken) -> None:
    home = taken.binary.home
    assert login_snapshot(taken.binary, "dispose") == {"disposed": True}
    assert not (home / AREA).exists()
    assert tree(home, area=True) == taken.before
    assert login_snapshot(taken.binary, "dispose") == {"disposed": False}
