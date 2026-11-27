"""The login import's snapshot seen from outside (the L1b slice of the L1
plan, DESIGN §40.54 items 12, 14 and 22), written with no product code.

The hidden `neosian login-snapshot` verb takes a snapshot of the home
`NEOSIAN_HOME` names, measures its units and, with `--bodies DIR`, writes
every body it would send as `UUUU-PPPP.json` (unit index, part index).
What each unit must be is computed here from the home's files alone:

- A memory scope's body is the `store/restore_scope` request. Its digest is
  the SHA-256 of the body's bytes and its length is theirs, and it carries
  the archive's version rows and each live document's last content.
- A conversation holding a turn is its restore stream in parts: each
  turn's header then its records with the turn in front, the projections in
  the store's order, the trail acts in stored order (`differential.py`'s
  readers). Its digest is item 12's unit digest over that stream, and its
  length is item 14's size rule for that restore into an empty target:
  every item, the bookkeeping, the committed marker and the conversation
  row, the sum of the `stored_bytes` a server answers.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.contract.records.differential import (
    acts,
    projections,
    scopes,
    turns,
)
from tests.contract.records.importing import restore_id
from tests.contract.records.support import compact, u
from tests.contract.support import Binary

AREA = "login-import"
PART_BYTES = 983_040
PART_ITEMS = 2_048
KINDS = ("turns", "records", "projections", "redactions")


def tree(root: Path, *, area: bool = False) -> dict[str, str]:
    """Every file under `root` by its relative path, with its SHA-256; the
    snapshot's own area is left out unless `area`."""
    found: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if area or relative.split("/")[0] != AREA:
            found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def login_snapshot(binary: Binary, action: str, bodies: Path | None = None) -> Any:
    """`neosian login-snapshot ACTION [--bodies DIR]`, its one JSON object."""
    more = [] if bodies is None else ["--bodies", str(bodies)]
    result = subprocess.run(
        [str(binary.executable), "login-snapshot", action, *more],
        env=binary.environment(),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1_200,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def part(bodies: Path, unit: int, number: int) -> bytes:
    return (bodies / f"{unit:04}-{number:04}.json").read_bytes()


def check_scope(root: Path, unit: dict[str, Any], raw: bytes) -> None:
    """The scope's body: its digest and length, its rows and documents."""
    assert unit["sha256"] == hashlib.sha256(raw).hexdigest(), unit["name"]
    assert unit["bytes"] == len(raw), unit["name"]
    body = json.loads(raw)
    assert list(body) == ["scope", "documents", "versions", "redactions"]
    assert body["scope"] == unit["name"]
    archived = scopes(root)[unit["name"]]
    rows = [row for history in archived.values() for row in history]
    sent = sorted(body["versions"], key=lambda r: (r["path"], r["version"]))
    assert sent == sorted(rows, key=lambda r: (r["path"], r["version"]))
    contents = {d["path"]: d["content"] for d in body["documents"]}
    assert contents == {path: rows[-1]["content"] for path, rows in archived.items()}
    counts = (len(body["documents"]), len(sent), len(body["redactions"]))
    assert counts == (unit["documents"], unit["versions"], unit["redactions"])


class Stream:
    """One conversation's restore stream, read from the archive a turn at a
    time as the parts ask for it, with its digest and size rule."""

    def __init__(self, root: Path, conversation: str) -> None:
        self.root, self.conversation = root, conversation
        self.c, self.o = u(conversation), u(restore_id(conversation))
        self.turns = turns(root, conversation)
        self.queues: dict[str, deque[dict[str, Any]]] = {
            "turns": deque(),
            "records": deque(),
            "projections": deque(projections(root, conversation)),
            "redactions": deque(acts(root, conversation)),
        }
        self.digest = hashlib.sha256()
        self.counted = dict.fromkeys(KINDS, 0)
        # Bookkeeping, the committed marker and the conversation row.
        self.size = (self.o + 2 * self.c + 1_024) + (self.o + 2 * self.c + 256)
        self.size += self.c + 256

    def take(self, item: dict[str, Any]) -> None:
        self.digest.update(hashlib.sha256(compact(item)).digest())

    def pull(self) -> bool:
        turn = next(self.turns, None)
        if turn is None:
            return False
        header, c, o = turn.header, self.c, self.o
        self.take(header)
        if header["redacted"]:
            self.size += u(header["actor"]) + 2 * c + 256
        else:
            self.size += u(header["actor"]) + o + 4 * c + 512
        self.queues["turns"].append(header)
        for made in turn.records:
            item = {"turn": header["turn"], **made}
            self.take(item)
            named = u(made["call_id"]) + u(made["name"])
            self.size += u(made["text"]) + named + 2 * c + 256
            self.queues["records"].append(item)
        return True

    def expect(self, kind: str) -> dict[str, Any]:
        queue = self.queues[kind]
        while kind in ("turns", "records") and not queue:
            assert self.pull(), f"a part carries an extra item in {kind}"
        assert queue, f"a part carries an extra item in {kind}"
        self.counted[kind] += 1
        return queue.popleft()

    def finish(self) -> tuple[str, int]:
        """The digest and size once every part was read: the projections and
        the acts are hashed and charged after the turns."""
        while self.pull():
            pass
        assert not any(self.queues.values()), (self.conversation, "items missing")
        for entry in projections(self.root, self.conversation):
            self.take(entry)
            self.size += u(entry["text"]) + 2 * self.c + 128
        for act in acts(self.root, self.conversation):
            self.take(act)
            self.size += u(act["actor"]) + 4 * len(act["turns"]) + 2 * self.c + 128
        return self.digest.hexdigest(), self.size


def present(item: dict[str, Any]) -> dict[str, Any]:
    """An item's keys that hold a value: the encoder leaves a null out, and
    the wire reads an absent key as null."""
    return {key: value for key, value in item.items() if value is not None}


def parts(bodies: Path, index: int, unit: dict[str, Any]) -> Iterator[bytes]:
    for number in range(unit["parts"]):
        yield part(bodies, index, number)
    assert not (bodies / f"{index:04}-{unit['parts']:04}.json").exists()


def check_conversation(
    root: Path, bodies: Path, index: int, unit: dict[str, Any]
) -> None:
    """Every part within its bounds and carrying the next items of the
    stream, in order; the last one commits; the digest and the size rule
    are the archive's."""
    conversation = unit["name"]
    stream = Stream(root, conversation)
    places = {"projections": 0, "redactions": 0}
    last: dict[str, Any] = {}
    for raw in parts(bodies, index, unit):
        assert len(raw) <= PART_BYTES, conversation
        body = json.loads(raw)
        assert sum(len(body.get(kind, [])) for kind in KINDS) <= PART_ITEMS
        assert body["conversation_id"] == conversation
        assert body["restore"] == restore_id(conversation)
        for kind in KINDS:
            if kind in places and body.get(kind):
                assert body[f"{kind}_from"] == places[kind], (conversation, kind)
                places[kind] += len(body[kind])
            for item in body.get(kind, []):
                wanted = stream.expect(kind)
                assert present(item) == present(wanted), (conversation, kind, item)
        assert "commit" not in last, conversation
        last = body
    assert last.get("commit") is True, conversation
    assert (unit["sha256"], unit["bytes"]) == stream.finish(), conversation
    counted = [stream.counted[kind] for kind in ("turns", "projections", "redactions")]
    assert counted == [unit["turns"], unit["projections"], unit["redactions"]]
