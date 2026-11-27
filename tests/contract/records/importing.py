"""`neosian import DIR --url` into a server that keeps records (the T1e2e
slice of the T1e2 plan, DESIGN §40.54 items 12 and 22), written with no
product code.

An archive is a FileStore root whose rows are written here by hand. Its
restore stream (each turn's header and records, the projections in the
store's order, the trail acts in stored order), each item's position and
the unit digest are computed here from those rows alone: every message is
plain text under 64 KiB, so it is one record of its role's kind. The
binary runs behind the forwarding stub of `landing/stub.py`, which logs
every request with its raw body and can lose an answer or a request.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.contract.landing.stub import Exchange
from tests.contract.records.restoring import (
    POSITION_KEYS,
    RESTORE,
    act,
    header,
    placed,
    position,
    projection,
    unit_digest,
)
from tests.contract.records.support import compact, record
from tests.contract.support import ADMIN, Binary

PART_BYTES = 983_040
PART_ITEMS = 2_048
KINDS = {"user": "prompt", "assistant": "reply", "system": "system"}


def restore_id(conversation: str) -> str:
    """Item 22: `i` and the first 32 hex digits of SHA-256 of the id."""
    return "i" + hashlib.sha256(conversation.encode()).hexdigest()[:32]


def message(role: str, text: str) -> dict[str, Any]:
    return {
        "role": role,
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }


@dataclass
class Turn:
    number: int
    created_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    actor: str | None = None
    redacted: bool = False

    def row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "neosian_format": 1,
            "turn": self.number,
            "created_at": self.created_at,
            "messages": self.messages,
            "actor": self.actor,
        }
        if self.redacted:
            row["redacted"] = True
        return row

    def read(self, conversation: str) -> dict[str, Any]:
        """The turn as `read_turns` answers it."""
        return {
            "conversation_id": conversation,
            "turn": self.number,
            "messages": self.messages,
            "created_at": self.created_at,
            "actor": self.actor,
            "redacted": self.redacted,
        }


@dataclass
class Conversation:
    id: str
    turns: list[Turn]
    projections: list[dict[str, Any]] = field(default_factory=list)
    acts: list[dict[str, Any]] = field(default_factory=list)

    def ordered_projections(self) -> list[dict[str, Any]]:
        """The store's order: by turn, then span, insertion breaking ties."""
        return sorted(self.projections, key=lambda p: (p["turn"], p["span"]))

    def stream(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Every item in restore shape with the position after it."""
        items: list[tuple[dict[str, Any], dict[str, Any]]] = []
        last = position(0, 0, True)
        for turn in self.turns:
            if turn.redacted:
                head = header(turn.number, turn.created_at, turn.actor, 0, 0)
                head["redacted"] = True
                last = position(turn.number, 0, True)
                items.append((head, last))
                continue
            texts = [m["content"] for m in turn.messages]
            size = sum(len(text.encode()) for text in texts)
            head = header(turn.number, turn.created_at, turn.actor, len(texts), size)
            last = position(turn.number, 0, False)
            items.append((head, last))
            for seq, (msg, text) in enumerate(zip(turn.messages, texts, strict=True)):
                item = placed(
                    turn.number, record(seq, seq, msg["role"], KINDS[msg["role"]], text)
                )
                last = position(turn.number, seq + 1, seq + 1 == len(texts))
                items.append((item, last))
        for index, entry in enumerate(self.ordered_projections(), start=1):
            item = projection(
                entry["turn"], entry["kind"], entry["text"], entry["span"]
            )
            last = {**last, "projections": index}
            items.append((item, last))
        for index, row in enumerate(self.acts, start=1):
            item = act(self.id, row["turns"], row["actor"], row["created_at"])
            last = {**last, "redactions": index}
            items.append((item, last))
        return items

    def digest(self) -> str:
        return unit_digest([item for item, _ in self.stream()])

    def write(self, root: Path) -> None:
        directory = root / "conversations" / self.id
        directory.mkdir(parents=True)
        files = {
            "turns.jsonl": [turn.row() for turn in self.turns],
            "projections.jsonl": [
                {"neosian_format": 1, "turn": p["turn"], "span": p["span"]}
                | {"kind": p["kind"], "text": p["text"]}
                for p in self.projections
            ],
            "redactions.jsonl": [{"neosian_format": 1} | row for row in self.acts],
        }
        for name, rows in files.items():
            if rows:
                text = "".join(
                    json.dumps(row, separators=(",", ":")) + "\n" for row in rows
                )
                (directory / name).write_text(text, encoding="utf-8")

    def unit(self) -> dict[str, Any]:
        return {
            "kind": "conversation",
            "name": self.id,
            "documents": 0,
            "versions": 0,
            "redactions": len(self.acts),
            "turns": len(self.turns),
            "projections": len(self.projections),
        }


def act_row(index: int, turns: list[int]) -> dict[str, Any]:
    """Distinct acts whose times go back and forth, some without actor."""
    second = (index * 37) % 3_600
    time = (
        f"2026-09-02T{8 + second // 3_600:02}:{second // 60 % 60:02}:{second % 60:02}Z"
    )
    actor = None if index % 7 == 3 else f"cli:act-{index:05}"
    return {"created_at": time, "actor": actor, "turns": turns}


def rich(conversation: str = "import-rich", acts: int = 4_500) -> Conversation:
    """A conversation whose parts end after a header, inside a turn, after
    a projection and after an act, and cross the item cap: a large first
    turn, a redacted one, sparse turn numbers (4, 6 to 8 are absent), a turn
    longer than a part, four projections of 300,000 bytes and thousands of
    small acts."""
    actor = "claude-code:import"
    big = "x" * 60_000

    def talk(count: int, text: str) -> list[dict[str, Any]]:
        roles = ["user", "assistant"]
        return [message(roles[at % 2], f"{at:03}" + text[3:]) for at in range(count)]

    turns = [
        Turn(1, "2026-09-01T10:00:01Z", talk(16, big), actor),
        Turn(2, "2026-09-01T10:00:02Z", actor=actor, redacted=True),
        Turn(3, "2026-09-01T10:00:03.250000Z", talk(3, "y" * 50_000), None),
        Turn(5, "2026-09-01T10:00:05Z", [message("user", "w" * 44)], actor),
        Turn(9, "2026-09-01T10:00:09Z", talk(20, big), actor),
        Turn(10, "2026-09-01T10:00:10Z", [message("system", "be brief")], actor),
    ]
    projections = [
        {"turn": 9, "kind": "log", "text": "l" * 300_000, "span": 2},
        {"turn": 3, "kind": "epoch", "text": "e" * 300_000, "span": 3},
        {"turn": 10, "kind": "digest", "text": "d" * 300_000, "span": 1},
        {"turn": 9, "kind": "digest", "text": "g" * 300_000, "span": 1},
    ]
    rows = [act_row(index, [2]) for index in range(acts)]
    return Conversation(conversation, turns, projections, rows)


def run(
    binary: Binary, archive: Path, url: str, *more: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary.executable), "import", str(archive), "--url", url, "--json", *more],
        env=binary.environment() | {"NEOSIAN_CLIENT_TOKEN": ADMIN},
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def units(result: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    assert result.returncode == 0, result.stdout + result.stderr
    answer: list[dict[str, Any]] = json.loads(result.stdout)["units"]
    return answer


def failure(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 1, result.stdout + result.stderr
    error: str = json.loads(result.stdout)["error"]
    return error


def restores(log: list[Exchange], conversation: str | None = None) -> list[Exchange]:
    return [
        e
        for e in log
        if e.route == RESTORE
        and (conversation is None or e.body["conversation_id"] == conversation)
    ]


def is_probe(exchange: Exchange) -> bool:
    return set(exchange.body) == {"conversation_id", "restore"}


RECORD_KEYS = ["turn", "seq", "message", "role", "kind", "content"]
RECORD_KEYS += ["block", "call", "call_id", "name", "chunk", "size", "text"]


def items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """A part's items in stream order: by turn, a header before its turn's
    records; then the projections; then the acts. A record's null keys,
    which a request leaves out, are put back."""
    ordered: list[dict[str, Any]] = []
    records = [
        {key: r.get(key) for key in RECORD_KEYS} for r in body.get("records", [])
    ]
    for head in body.get("turns", []):
        while records and records[0]["turn"] < head["turn"]:
            ordered.append(records.pop(0))
        ordered.append(head)
    ordered.extend(records)
    ordered.extend(body.get("projections", []))
    ordered.extend(body.get("redactions", []))
    return ordered


def within_bounds(exchange: Exchange) -> None:
    """Every part the binary sent: at most 983,040 bytes and 2,048 items,
    its bytes the compact JSON of what it carries, `_from` beside a list."""
    assert len(exchange.raw) <= PART_BYTES, len(exchange.raw)
    assert len(items(exchange.body)) <= PART_ITEMS
    assert exchange.raw == compact(exchange.body)
    for name in ("projections", "redactions"):
        assert (name in exchange.body) == (f"{name}_from" in exchange.body)


def answered_position(exchange: Exchange) -> dict[str, Any] | None:
    answer = exchange.answer
    assert isinstance(answer, dict), exchange
    at: dict[str, Any] | None = answer["position"]
    if at is not None:
        assert list(at) == POSITION_KEYS
    return at
