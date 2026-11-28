"""Records memory gate: peak memory of each record operation, by input size.

DESIGN §40.54 item 16.2. A literal corpus goes through the five record routes
of a release `neosian serve`, over the wire only: turns of 1, 4 and 16 MiB in
two shapes (one tool result of that size; messages of 1.2 MiB), conversations
of 16, 32 and 64 MiB of 4 MiB turns, and one part of 2,048 tiny records. Each
measured sequence runs in a fresh server process and reads that process's
peak; what it needs was prepared by an earlier process. A FileStore is
enabled with `neosian records enable`; PostgreSQL gets schema 4 and records
extension 1 in a disposable schema. A sweep finds a draft stale because the
bench dates its `touched_at` to 2000, as the contract tests do.

Flatness: for each operation and series, the peak at the largest size minus
the peak at the smallest is at most 2 MiB, and every peak is within its
budget. The budget formula was declared before any measurement: B plus the
item 16.1 term at the test's part size, rounded up to 8 MiB. Seal timing:
in one warm server per backend, sizes alternate over fresh drafts after a
warm-up, and the median 16 MiB seal is at most twice the median 1 MiB seal
(each sample logged). Commit durations are reported without a ratio gate.
Every answer is read back against the corpus; a missing capability, a
refusal, an empty page or an incomplete read fails the gate.

Steady state, ruling K4 (2026-11-28). Flatness is a steady-state property.
A cold PostgreSQL process peaks lower (18.7 MiB after one part) and rises to
a plateau over its first parts, as its four pooled connections and its
caches fill; a FileStore process does the same by about 6 MiB. So every
gated process, on both stores, first sends full parts until its resident
size has risen by at most 256 KiB over the last eight parts, at most 64
parts, then discards that draft and runs the measured sequence. A process
that never levels fails the gate and names its operation (the leak check);
the parts each process needed are printed. Each gated case runs in five
fresh processes, each sample logged, and a case with fewer than five fails.
Flatness compares the median peaks of the largest and smallest sizes, at
most 2 MiB apart. A cold pass of one sample per case, without the warm-up,
is printed on both stores and not gated.

Ceilings, ruling K5 (2026-11-28). A process that has served parts keeps
about 6 MiB on a FileStore and its pool's buffers on PostgreSQL, so the
design's budgets, which start from an idle process, are printed and the
steady ceilings are gated. One ceiling per store is checked against a
case's median peak, and no single sample may pass it by more than 2 MiB: a
FileStore 24 MiB for every operation; PostgreSQL 40 MiB for parts and 32 MiB
for the other operations (the item 16.1 budget plus `POOL_CONNECTIONS x
4 MiB`, the pool size being `CONNECTIONS` in
`crates/neosian/src/postgres/connection.rs`, which the gate checks). A
FileStore operation's item 16.1 figure of 16 MiB is printed beside it as the
idle-process budget, not gated.

Regression guards, ruling K7 (2026-11-28). The first CI run on Linux showed
steady figures 1 to 2 MiB above the macOS ones the ceilings were declared
from, and one flatness of 2.42 MiB. The gate therefore guards against
regressions with headroom: flatness by medians at most 4 MiB, and one
ceiling per store by medians, 32 MiB on a FileStore and 48 MiB on PostgreSQL
for every operation, with a single sample at most 2 MiB over. The design's
2 MiB flatness target is reported per operation, as are the macOS-declared
ceilings of K5 and item 16.1's idle-process budgets, each marked met or not
met; on Linux the flatness target is not met for FileStore restore parts.

Warm-up by route, ruling K8 (2026-11-28). A restore part's memory steps
once over a process's first restore parts and then levels, from 1 MiB to
128 MiB restores at 18.5 to 19.6 MiB on files and 29.0 to 30.2 on PostgreSQL
(record/ci-repairs-2026-11-28/restore-memory.md), and a warm-up made of
write parts left that step inside the size comparison. So each process is
warmed with the route it measures: restore-part and commit cases stage a
throwaway restore of messages until level and discard it, the others warm
with write parts.

The protocol changed seven times after a run, each for a stated reason of
measurement and none to fit a product number:
1. K1, the pool term: the formula omitted memory a store keeps between
   requests (PostgreSQL's pooled connection buffers), so PostgreSQL budgets
   add it.
2. K2, medians for flatness: fresh processes scatter by about 1.2 MiB either
   way at every size, which one process per size cannot tell from growth.
3. K3, medians for budgets with a bounded outlier: a lone process peaks 1.2
   to 1.6 MiB above its usual value at any size, so checking the largest of
   five samples failed one run in three on noise; a sample more than 2 MiB
   over the budget still fails.
4. K4, a steady state: memory rises over a process's first parts and then
   stops, so sizes are compared once it has levelled, and a process that
   never levels fails.
5. K5, one ceiling per store: a FileStore process at steady state holds about
   18 MiB, over the 16 MiB idle-process budgets, so those are printed and a
   24 MiB ceiling is gated; PostgreSQL keeps K1's figures.
6. K7, regression guards: Linux runs 1 to 2 MiB above macOS, so the gate
   guards with headroom (4 MiB flatness; 32 and 48 MiB ceilings) and reports
   the design's targets and the macOS-declared ceilings as met or not met.
7. K8, warm-up by route: a restore part steps once over its first parts, so
   restore and commit cases warm with a throwaway restore.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bench_search_memory import (  # noqa: E402
    DSN_ENV,
    TOKEN,
    Backend,
    _drop,
    _peak,
    _server,
)

MIB: Final = 1 << 20
KIB: Final = 1 << 10
# Budget inputs (DESIGN §40.54 item 16.1), declared before measuring.
BASE: Final = 10.5  # B, MiB: the idle server (§40.46)
PART: Final = 983_040  # P: the test's part size, the binary's largest body
RECORD: Final = 81_920  # R: one record as written or answered
SPOOL: Final = MIB  # G: the part of an answer held in memory
FORMAT_ONE: Final = 0  # L: the corpus holds no format-1 turn
CANDIDATE: Final = 64  # bytes a relevance candidate keeps
HIT: Final = 16 * KIB  # a newest search's key and winner
SWEPT: Final = 2 * KIB  # a swept conversation
STEP: Final = 8.0  # MiB a budget is rounded up to
FLAT: Final = 4.0  # MiB the median rises by at most, the gate's guard (K7)
FLAT_TARGET: Final = 2.0  # MiB, the design's flatness target, reported (K7)
SAMPLES: Final = 5  # fresh processes per gated case (K2)
OUTLIER: Final = 2.0  # MiB one sample may pass its ceiling by (K3)
FILE_CEILING: Final = 24.0  # MiB, K5's macOS-declared FileStore ceiling, reported
FILE_GUARD: Final = 32.0  # MiB, every FileStore operation, gated (K7)
POSTGRES_GUARD: Final = 48.0  # MiB, every PostgreSQL operation, gated (K7)
RATIO: Final = 2.0  # median 16 MiB seal over median 1 MiB seal
ROUNDS: Final = 7  # timing samples per size and shape
LIMIT: Final = 20  # search limit, the same at every size
SWEEP_LIMIT: Final = 50  # the route's default
POOL_SOURCE: Final = ROOT / "crates/neosian/src/postgres/connection.rs"
POOL_CONNECTIONS: Final = 4  # its CONNECTIONS; the budgets are declared for 4
POOL_BUFFER: Final = 4 * MIB  # what a connection keeps after one part (K1)
LEVEL: Final = 0.25  # MiB of resident rise allowed over the last LEVEL_PARTS
LEVEL_PARTS: Final = 8  # full parts the resident size must stay level over
WARM_LIMIT: Final = 64  # full parts after which a process that has not levelled fails
# Corpus settings, frozen with the budgets.
TURN_SIZES: Final = (1 * MIB, 4 * MIB, 16 * MIB)
CONVERSATION_SIZES: Final = (16 * MIB, 32 * MIB, 64 * MIB)
CONVERSATION_TURN: Final = 4 * MIB
MESSAGE: Final = 1_258_291  # 1.2 MiB
TINY_RECORDS: Final = 2_048
CHUNK: Final = 60_000  # ASCII bytes a chunk holds, under 65,536 encoded
TERM: Final = "zq"  # in every record
PROMPT: Final = f"run the {TERM} tests"
ARGS: Final = '{"command":"make ' + TERM + '"}'
REPLY: Final = f"All {TERM} tests pass."
CALL: Final = "toolu_01"
STAMP: Final = "2026-10-02T09:15:00Z"
ACTOR: Final = "cli:bench"
HEADS: Final = {
    "prompt": 16_384,
    "reply": 16_384,
    "tool_call": 1_024,
    "tool_result": 2_048,
}
LIGHT: Final[dict[str, Any]] = {"kinds": list(HEADS), "heads": HEADS}  # light recall
WRITES, READS = "conversation/write_records", "conversation/read_records"
RESTORES, SEARCHES = "store/restore_records", "conversation/search_records"
WRITE, SEAL, TINY_PART = "write part", "seal", "write tiny part"
RESTORE, COMMIT = "restore part", "commit"
FULL, LIGHT_READ, PIECE = "read full", "read light", "piece fetch"
NEWEST, RELEVANCE, SWEEP = "search newest", "search relevance", "sweep preview"
RESULT, MESSAGES, CONVERSATION, TINY = "result", "messages", "conversation", "tiny"
SHAPES: Final = (RESULT, MESSAGES)
TERMS: Final = {  # bytes over B for each operation (item 16.1)
    WRITE: 6 * PART + 4 * RECORD,
    TINY_PART: 6 * PART + 4 * RECORD,
    SEAL: 4 * RECORD,
    RESTORE: 6 * PART + 4 * RECORD,
    COMMIT: 4 * RECORD,
    FULL: SPOOL + 4 * RECORD + 4 * FORMAT_ONE,
    LIGHT_READ: SPOOL + 4 * RECORD + 4 * FORMAT_ONE,
    PIECE: SPOOL + 4 * RECORD + 4 * FORMAT_ONE,
    NEWEST: LIMIT * HIT + 4 * RECORD + 4 * FORMAT_ONE,
    SWEEP: SWEEP_LIMIT * SWEPT,
}
Piece = tuple[str, str, int, dict[str, Any]]  # role, kind, bytes, other keys
Post = Callable[[str, dict[str, Any]], dict[str, Any]]


class GateFailure(Exception):
    """A refusal, a missing capability or an answer that does not read back."""


@dataclass(frozen=True)
class Case:
    operation: str
    series: str
    size: int  # bytes of the turn or conversation; records of the tiny part


@dataclass
class Measured:
    case: Case
    peak: float
    seconds: float
    requests: int
    items: int
    warm: bool = False  # measured after the pool warm-up
    sample: int = 0  # which of the case's fresh processes
    warm_parts: int = 0  # full parts the warm-up sent
    levelled: bool = True  # the resident size levelled within WARM_LIMIT parts
    hwm: list[float] = field(default_factory=list)  # after each request, Linux


def matrix() -> tuple[Case, ...]:
    """Every measured case, in the order the workload runs them."""
    cases = [Case(op, s, n) for s in SHAPES for n in TURN_SIZES for op in (WRITE, SEAL)]
    cases.append(Case(TINY_PART, TINY, TINY_RECORDS))
    cases += [Case(PIECE, RESULT, size) for size in TURN_SIZES]
    for series in (*SHAPES, CONVERSATION):
        for size in CONVERSATION_SIZES if series == CONVERSATION else TURN_SIZES:
            cases += [Case(RESTORE, series, size), Case(COMMIT, series, size)]
    for op in (FULL, LIGHT_READ, NEWEST, RELEVANCE):
        cases += [Case(op, CONVERSATION, size) for size in CONVERSATION_SIZES]
    cases += [Case(SWEEP, RESULT, size) for size in TURN_SIZES]
    return tuple(cases)


def pieces(shape: str, size: int) -> list[Piece]:
    """The pieces of one turn; their bytes add up to `size`."""
    if shape == TINY:
        return [("user", "prompt", 1, {"text": "a"})] * size
    if shape == RESULT:
        body = size - len(PROMPT) - len(ARGS) - len(REPLY)
        call = {"content": "none", "call": 0, "call_id": CALL, "name": "Bash"}
        return [
            ("user", "prompt", len(PROMPT), {"text": PROMPT}),
            ("assistant", "tool_call", len(ARGS), {**call, "text": ARGS}),
            ("tool", "tool_result", body, {"call_id": CALL}),
            ("assistant", "reply", len(REPLY), {"text": REPLY}),
        ]
    lengths = [MESSAGE] * (size // MESSAGE) + [size % MESSAGE] * bool(size % MESSAGE)
    roles = (("user", "prompt"), ("assistant", "reply"))
    return [(*roles[n % 2], length, {}) for n, length in enumerate(lengths)]


def turns_of(case: Case) -> list[list[Piece]]:
    """A case's turns: one, or a conversation of 4 MiB turns of both shapes."""
    if case.series != CONVERSATION:
        return [pieces(case.series, case.size)]
    count = case.size // CONVERSATION_TURN
    return [pieces(SHAPES[n % 2], CONVERSATION_TURN) for n in range(count)]


def chunks(length: int) -> int:
    return max(1, math.ceil(length / CHUNK))


def record_count(turns: list[list[Piece]]) -> int:
    return sum(chunks(piece[2]) for turn in turns for piece in turn)


def budget(case: Case, pool: int = 0) -> float:
    """MiB: B plus the operation's item 16.1 term plus `pool` pooled
    connections' buffers (K1, PostgreSQL), rounded up to 8 MiB."""
    if case.operation == RELEVANCE:
        term = CANDIDATE * record_count(turns_of(case)) + 4 * RECORD + 4 * FORMAT_ONE
    else:
        term = TERMS[case.operation]
    term += pool * POOL_BUFFER
    return math.ceil((BASE + term / MIB) / STEP) * STEP


def ceiling(pool: int = 0) -> float:
    """The gated steady-state ceiling (K7), one per store."""
    return POSTGRES_GUARD if pool else FILE_GUARD


def declared(case: Case, pool: int = 0) -> float:
    """K5's macOS-declared ceiling, reported: a FileStore's one figure, or on
    PostgreSQL the item 16.1 budget with the pool term."""
    return budget(case, pool) if pool else FILE_CEILING


def met(value: float, target: float) -> str:
    return "met" if value <= target else "not met"


def flatness(peaks: dict[int, float]) -> tuple[float, bool]:
    """(the largest size's median peak minus the smallest's, within FLAT)."""
    spread = peaks[max(peaks)] - peaks[min(peaks)]
    return spread, spread <= FLAT


def samples_of(results: list[Measured]) -> dict[Case, list[float]]:
    samples: dict[Case, list[float]] = {}
    for m in results:
        samples.setdefault(m.case, []).append(m.peak)
    return samples


def series_peaks(results: list[Measured]) -> dict[tuple[str, str], dict[int, float]]:
    """Each series' median peak by size."""
    series: dict[tuple[str, str], dict[int, float]] = {}
    for case, peaks in samples_of(results).items():
        median = statistics.median(peaks)
        series.setdefault((case.operation, case.series), {})[case.size] = median
    return series


def verdicts(results: list[Measured], pool: int = 0) -> list[str]:
    """Every failure: a case not measured SAMPLES times, or (on a pooled
    store) without the warm-up, a median over its ceiling, a sample more than
    OUTLIER over it, medians rising past FLAT."""
    failures = [
        f"not measured: {c.operation} {c.series} {label(c)}" for c in missing(results)
    ]
    cold = {m.case for m in results if not m.warm}
    failures += [
        f"{m.case.operation} {m.case.series} {label(m.case)} #{m.sample}: resident"
        f" size never levelled within {WARM_LIMIT} parts"
        for m in results
        if not m.levelled
    ]
    for case, peaks in samples_of(results).items():
        name = f"{case.operation} {case.series} {label(case)}"
        if len(peaks) != SAMPLES:
            failures.append(f"{name}: {len(peaks)} samples, not {SAMPLES}")
        if case in cold:
            failures.append(f"{name}: no steady-state warm-up")
        limit = ceiling(pool)
        if statistics.median(peaks) > limit:
            failures.append(
                f"{name}: median peak {statistics.median(peaks):.1f} MiB over its"
                f" ceiling of {limit:.0f}"
            )
        if max(peaks) > limit + OUTLIER:
            failures.append(
                f"{name}: a peak of {max(peaks):.1f} MiB passes its ceiling of"
                f" {limit:.0f} by more than {OUTLIER:.0f} MiB"
            )
    for (operation, series), medians in series_peaks(results).items():
        spread, flat = flatness(medians)
        if len(medians) > 1 and not flat:
            failures.append(f"{operation} {series}: not flat, rose {spread:.2f} MiB")
    return failures


def missing(results: list[Measured]) -> list[Case]:
    done = {m.case for m in results}
    return [case for case in matrix() if case not in done]


def ratio(samples: dict[int, list[float]]) -> tuple[float, bool]:
    """(the median 16 MiB seal over the median 1 MiB seal, within RATIO)."""
    small, large = samples[min(TURN_SIZES)], samples[max(TURN_SIZES)]
    value = statistics.median(large) / statistics.median(small)
    return value, value <= RATIO


def require_pool(source: str) -> None:
    """The budgets hold for the pool size they were declared for."""
    found = re.findall(r"const CONNECTIONS: usize = (\d+);", source)
    if found != [str(POOL_CONNECTIONS)]:
        raise GateFailure(
            f"the PostgreSQL pool is {found}, the budgets are declared for"
            f" {POOL_CONNECTIONS} connections: redeclare POOL_CONNECTIONS"
        )


def require_records(capabilities: dict[str, Any]) -> None:
    if capabilities.get("turn_records") != {"version": 1}:
        raise GateFailure(f"no turn_records version 1 in {sorted(capabilities)}")


def label(case: Case) -> str:
    return f"{case.size} records" if case.series == TINY else f"{case.size // MIB} MiB"


@cache
def _unit() -> str:
    return "".join(f"{TERM} line {n:05d} deploy review latency\n" for n in range(2000))


def records(turn: list[Piece], number: int | None = None) -> list[dict[str, Any]]:
    """One turn's records in write shape, or restore shape with `number`."""
    out: list[dict[str, Any]] = []
    for message, (role, kind, length, extra) in enumerate(turn):
        text = extra.get("text") or (_unit() * (length // len(_unit()) + 1))[:length]
        for chunk in range(chunks(length)):
            item: dict[str, Any] = {} if number is None else {"turn": number}
            item.update(seq=len(out), message=message, role=role, kind=kind)
            item.update(content=extra.get("content", "text") if chunk == 0 else None)
            item.update(
                block=None, call=extra.get("call"), call_id=extra.get("call_id")
            )
            item.update(name=extra.get("name"), chunk=chunk, size=length)
            item["text"] = text[chunk * CHUNK : (chunk + 1) * CHUNK]
            out.append(item)
    return out


def _dumps(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def parts(
    fixed: dict[str, Any], items: list[dict[str, Any]], opening: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bodies of at most PART bytes: `fixed` in each, `opening` in the first."""
    out: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    overhead, used = len(_dumps({**fixed, **opening, "records": []})), 0
    for item in [*items, None]:
        length = 0 if item is None else len(_dumps(item)) + 1
        if batch and (item is None or overhead + used + length > PART):
            out.append({**fixed, **({} if out else opening), "records": batch})
            batch, used = [], 0
        if item is not None:
            batch.append(item)
            used += length
    return out


def item_bytes(conversation: str, item: dict[str, Any]) -> int:
    """Item 14's size rule for one record."""
    named = len(item["call_id"] or "") + len(item["name"] or "")
    return len(item["text"]) + named + 2 * len(conversation) + 256


def check_pages(pages: list[dict[str, Any]], turns: int, count: int, text: int) -> int:
    """The records of every page against what the corpus holds."""
    seen = [item for page in pages for item in page["records"]]
    numbers = {header["turn"] for page in pages for header in page["turns"]}
    if any(not page["records"] and not page["turns"] for page in pages):
        raise GateFailure("an empty page")
    if not pages or pages[-1]["continuation"] is not None:
        raise GateFailure("the read stopped before its last page")
    total = sum(len(item["text"]) for item in seen)
    if (len(numbers), len(seen), total) != (turns, count, text):
        raise GateFailure(
            f"read back {len(numbers)} turns, {len(seen)} records, {total} bytes;"
            f" the corpus holds {turns}, {count}, {text}"
        )
    return len(seen)


def check_hits(answer: dict[str, Any], conversation: str, order: str) -> int:
    hits = answer["hits"]
    if len(hits) != LIMIT or {hit["conversation_id"] for hit in hits} != {conversation}:
        raise GateFailure(f"{len(hits)} hits, not {LIMIT} in {conversation}")
    if order == "relevance" and (
        answer.get("order") != order or any("score" not in hit for hit in hits)
    ):
        raise GateFailure("a relevance answer without its order or scores")
    return len(hits)


def expect(answer: dict[str, Any], **wanted: Any) -> None:
    got = {key: answer.get(key) for key in wanted}
    if got != wanted:
        raise GateFailure(f"answered {got}, expected {wanted}")


def _request(url: str, route: str, body: dict[str, Any] | None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url}/v1/{route}",
        data=None if body is None else _dumps(body),
        headers={"Authorization": f"Bearer {TOKEN}"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            answer: dict[str, Any] = json.loads(response.read())
            return answer
    except urllib.error.HTTPError as error:
        raise GateFailure(f"{route}: {error.code} {error.read()[:300]!r}") from None


def _hwm(pid: int) -> list[float]:
    status = Path(f"/proc/{pid}/status")
    lines = status.read_text().splitlines() if status.is_file() else []
    return [int(line.split()[1]) / KIB for line in lines if line.startswith("VmHWM:")]


@dataclass
class Running:
    """A server, the requests sent to it and its peak after each (Linux)."""

    url: str
    process: subprocess.Popen[bytes]
    requests: int = 0
    hwm: list[float] = field(default_factory=list)

    def post(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        answer = _request(self.url, route, body)
        self.requests += 1
        self.hwm += _hwm(self.process.pid)
        return answer


@dataclass
class Store:
    name: str
    binary: Path
    backend: Backend
    home: Path | None = None
    dsn: str = ""
    schema: str = ""
    pool: int = 0  # pooled connections whose buffers the budgets count

    @contextmanager
    def serve(self) -> Iterator[Running]:
        """A fresh server process."""
        with _server(self.binary, self.backend) as (url, process):
            yield Running(url, process)

    def age(self, conversation: str, draft: str) -> None:
        """Date an open draft to 2000 so that a sweep finds it stale."""
        if self.home is not None:
            path = self.home / "conversations" / conversation / ".drafts"
            path /= f"{draft}.state"
            old = '"touched_at":"2000-01-01T00:00:00Z"'
            text, count = re.subn(r'"touched_at":"[^"]*"', old, path.read_text())
            if count != 1:
                raise GateFailure(f"no touched_at in {path}")
            path.write_text(text)
            return
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            statement = sql.SQL(
                "UPDATE {}.record_drafts SET touched_at = '2000-01-01T00:00:00Z'"
                " WHERE conversation_id = %s AND name = %s"
            ).format(sql.Identifier(self.schema))
            if connection.execute(statement, (conversation, draft)).rowcount != 1:
                raise GateFailure(f"no draft {draft} in {conversation}")


def conversation_of(case: Case, cold: bool = False, sample: int = 0) -> str:
    """Writes and seals make what a piece fetch reads (turn 1, each sample a
    draft of its own); each sample restores and reads a conversation of its own."""
    prefix = "w" if case.operation in (WRITE, SEAL, TINY_PART, PIECE) else "r"
    prefix = "s" if case.operation == SWEEP else prefix
    own = f"-{sample}" * (prefix == "r")
    return f"{'cold-' * cold}{prefix}-{case.series}-{case.size // MIB}{own}"


def level_after(resident: list[float]) -> int | None:
    """The first part count after which the resident size (MiB, before any
    part then after each) rose by at most LEVEL over the last LEVEL_PARTS."""
    for parts_sent in range(LEVEL_PARTS, len(resident)):
        window = resident[parts_sent - LEVEL_PARTS : parts_sent + 1]
        if max(window) - window[0] <= LEVEL:
            return parts_sent
    return None


@cache
def warm_bodies(route: str = WRITES) -> tuple[dict[str, Any], ...]:
    """The first WARM_LIMIT full parts of the warm-up for `route`: a 64 MiB
    tool result turn as a draft, or a 64 MiB turn of messages as a restore."""
    if route == RESTORES:
        turn = pieces(MESSAGES, 64 * MIB)
        header = {"turn": 1, "created_at": STAMP, "actor": ACTOR, "redacted": False}
        header.update(records=record_count([turn]), bytes=64 * MIB)
        fixed = {"conversation_id": "warm-up", "restore": "warm"}
        bodies = parts(fixed, records(turn, 1), {"turns": [header]})
    else:
        turn = pieces(RESULT, 64 * MIB)
        fixed = {"conversation_id": "warm-up", "draft": "warm"}
        bodies = parts(fixed, records(turn), {"turn_bytes": 64 * MIB})
    return tuple(bodies[:WARM_LIMIT])


def warm_route(case: Case) -> str:
    """The route a case's process is warmed with: the one it measures (K8)."""
    return RESTORES if case.operation in (RESTORE, COMMIT) else WRITES


WARM_DRAFTS: Final = itertools.count()


def warm_up(
    post: Post, resident: Callable[[], float], route: str = WRITES
) -> tuple[int, bool]:
    """Full parts on `route` until the resident size is level, then the draft
    or the throwaway restore discarded: (parts sent, levelled)."""
    name, seen = f"warm{next(WARM_DRAFTS)}", [resident()]
    key = "restore" if route == RESTORES else "draft"
    conversation = f"warm-up-{name}" if route == RESTORES else "warm-up"
    sent, levelled = 0, False
    for body in warm_bodies(route):
        part = {**body, "conversation_id": conversation, key: name}
        expect(post(route, part), state="open")
        sent += 1
        seen.append(resident())
        if level_after(seen) is not None:
            levelled = True
            break
    body = {"conversation_id": conversation, key: name, "discard": True}
    if route == WRITES:
        body["records"] = []
    expect(post(route, body), state="discarded")
    return sent, levelled


def write(post: Post, conversation: str, draft: str, turn: list[Piece]) -> int:
    """Every part of a draft, each answer read back; no seal."""
    items = records(turn)
    total, stored = sum(piece[2] for piece in turn), len(draft) + 2 * len(conversation)
    stored += 1024  # the opening's bookkeeping
    fixed = {"conversation_id": conversation, "draft": draft}
    for body in parts(fixed, items, {"turn_bytes": total}):
        stored += sum(item_bytes(conversation, item) for item in body["records"])
        answer = post(WRITES, body)
        expect(answer, state="open", next_seq=body["records"][-1]["seq"] + 1)
        expect(answer, stored_bytes=stored)
        stored = 0
    return len(items)


def seal(post: Post, conversation: str, draft: str, turn: list[Piece]) -> int:
    body = {
        "conversation_id": conversation,
        "draft": draft,
        "records": [],
        "seal": True,
    }
    answer = post(WRITES, body)
    expect(answer, state="sealed", next_seq=record_count([turn]))
    expect(answer["turn"], records=record_count([turn]), bytes=sum(p[2] for p in turn))
    return 1


def restore(post: Post, conversation: str, name: str, turns: list[list[Piece]]) -> int:
    """Every part of a restore, each answer read back; no commit."""
    fixed = {"conversation_id": conversation, "restore": name}
    for number, turn in enumerate(turns, start=1):
        count, size = record_count([turn]), sum(piece[2] for piece in turn)
        header = {"turn": number, "created_at": STAMP, "actor": ACTOR}
        header.update(redacted=False, records=count, bytes=size)
        for body in parts(fixed, records(turn, number), {"turns": [header]}):
            seq = body["records"][-1]["seq"] + 1
            position = {"turn": number, "seq": seq, "complete": seq == count}
            position.update(projections=0, redactions=0)
            expect(post(RESTORES, body), state="open", position=position)
    return record_count(turns)


def commit(post: Post, conversation: str, name: str, turns: list[list[Piece]]) -> int:
    body = {"conversation_id": conversation, "restore": name, "commit": True}
    answer = post(RESTORES, body)
    expect(answer, state="committed")
    expect(answer["position"], turn=len(turns), complete=True)
    if not isinstance(answer["digest"], str):
        raise GateFailure("a commit without its digest")
    return 1


def walk(post: Post, body: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    while not pages or pages[-1]["continuation"] is not None:
        if pages:
            body = {**body, "continuation": pages[-1]["continuation"]}
        pages.append(post(READS, body))
        if not pages[-1]["records"] and not pages[-1]["turns"]:
            raise GateFailure("an empty page")
    return pages


def drive(case: Case, post: Post, cold: bool = False, sample: int = 0) -> int:
    """The measured requests of one case, read back."""
    conversation, turns = conversation_of(case, cold, sample), turns_of(case)
    if case.operation in (WRITE, TINY_PART):
        return write(post, conversation, f"d{sample}", turns[0])
    if case.operation == SEAL:
        return seal(post, conversation, f"d{sample}", turns[0])
    if case.operation == RESTORE:
        return restore(post, conversation, "r1", turns)
    if case.operation == COMMIT:
        return commit(post, conversation, "r1", turns)
    if case.operation == PIECE:
        body = {"conversation_id": conversation, "turn": 1, "seq": 2, "piece": True}
        return check_pages(walk(post, body), 1, chunks(turns[0][2][2]), turns[0][2][2])
    if case.operation == FULL:
        pages = walk(post, {"conversation_id": conversation})
        return check_pages(pages, len(turns), record_count(turns), case.size)
    if case.operation == LIGHT_READ:
        pages = walk(post, {"conversation_id": conversation, **LIGHT})
        seen = [item for page in pages for item in page["records"]]
        wanted = [p[2] > 2 * HEADS[p[1]] for turn in turns for p in turn]
        if [item["truncated"] for item in seen] != wanted or pages[-1]["continuation"]:
            raise GateFailure(f"light read back {len(seen)} of {len(wanted)} pieces")
        return len(seen)
    if case.operation in (NEWEST, RELEVANCE):
        order = case.operation.split()[1]
        body = {"query": TERM, "conversations": [conversation], "order": order}
        return check_hits(post(SEARCHES, {**body, "limit": LIMIT}), conversation, order)
    answer = post("store/sweep_records", {"limit": SWEEP_LIMIT})
    items = records(turns[0])
    freed = len(f"stale{sample}") + 2 * len(conversation) + 1024
    freed += sum(item_bytes(conversation, item) for item in items)
    swept = {"conversation_id": conversation, "drafts": 1, "restores": 0}
    expect(answer, conversations=[{**swept, "freed_bytes": freed}], write=False)
    return 1


def measure(
    store: Store, case: Case, warm: bool, cold: bool = False, sample: int = 0
) -> Measured:
    """One sample of a case in a fresh server, prepared and cleaned up by
    others; `warm` runs the steady-state warm-up first, `cold` names the cold
    pass's conversations."""
    conversation, turn = conversation_of(case, cold, sample), turns_of(case)[0]
    stale = f"stale{sample}"
    if case.operation == SWEEP:
        with store.serve() as server:
            write(server.post, conversation, stale, turn)
        store.age(conversation, stale)
    with store.serve() as server:
        sent, levelled = 0, True
        if warm:
            pid = server.process.pid
            route = warm_route(case)
            sent, levelled = warm_up(server.post, lambda: _rss(pid), route)
        before = server.requests
        started = time.perf_counter()
        items = drive(case, server.post, cold, sample)
        seconds = time.perf_counter() - started
        peak = _peak(server.process)
    if case.operation == SWEEP:
        with store.serve() as cleanup:
            body = {"conversation_id": conversation, "draft": stale, "records": []}
            expect(cleanup.post(WRITES, {**body, "discard": True}), state="discarded")
    requests = server.requests - before
    return Measured(
        case, peak, seconds, requests, items, warm, sample, sent, levelled, server.hwm
    )


def _rss(pid: int) -> float:
    """The process's resident size now, in MiB (Linux /proc, macOS libproc)."""
    status = Path(f"/proc/{pid}/status")
    if status.is_file():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / KIB
    import ctypes

    info = ctypes.create_string_buffer(96)  # struct proc_taskinfo
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    if libproc.proc_pidinfo(pid, 4, ctypes.c_uint64(0), info, 96) != 96:
        raise GateFailure(f"no resident size for process {pid}")
    return int.from_bytes(info.raw[8:16], "little") / MIB


def timing(
    store: Store,
) -> tuple[dict[str, dict[int, list[float]]], dict[int, list[float]]]:
    """Seal and commit durations in one warm server, sizes alternating."""
    seals: dict[str, dict[int, list[float]]] = {
        shape: {size: [] for size in TURN_SIZES} for shape in SHAPES
    }
    commits: dict[int, list[float]] = {size: [] for size in TURN_SIZES}
    small = [("user", "prompt", len(PROMPT), {"text": PROMPT})]
    with store.serve() as server:
        post = server.post
        for shape in SHAPES:  # the database connection and conversation rows
            for size in TURN_SIZES:
                write(post, f"t-{shape}-{size // MIB}", "warm", small)
                seal(post, f"t-{shape}-{size // MIB}", "warm", small)
        restore(post, "c-warm", "r1", [small])
        commit(post, "c-warm", "r1", [small])
        for round_ in range(ROUNDS):
            shift = round_ % len(TURN_SIZES)
            for size in TURN_SIZES[shift:] + TURN_SIZES[:shift]:
                for shape in SHAPES if round_ % 2 == 0 else SHAPES[::-1]:
                    conversation, turn = f"t-{shape}-{size // MIB}", pieces(shape, size)
                    write(post, conversation, f"d{round_}", turn)
                    started = time.perf_counter()
                    seal(post, conversation, f"d{round_}", turn)
                    seals[shape][size].append(time.perf_counter() - started)
                conversation, turn = f"c-{size // MIB}-{round_}", pieces(RESULT, size)
                restore(post, conversation, "r1", [turn])
                started = time.perf_counter()
                commit(post, conversation, "r1", [turn])
                commits[size].append(time.perf_counter() - started)
    return seals, commits


def _samples(what: str, seconds: list[float]) -> str:
    values = " ".join(f"{value * 1000:.2f}" for value in seconds)
    return f"{what} ms: {values}  median {statistics.median(seconds) * 1000:.2f}"


def _row(name: str, m: Measured, limit: float, verdict: str, pool: int) -> str:
    case, macos, idle = m.case, declared(m.case, pool), budget(m.case)
    return (
        f"{name:<16}{case.operation:<17}{case.series:<13}{label(case):>13} #{m.sample}"
        f"{m.requests:>5}{m.items:>7}{m.seconds:>8.2f}{m.peak:>9.1f}"
        f"{limit:>8.0f}  {verdict}"
        + (f"  (warm-up {m.warm_parts} parts)" * m.warm)
        + f"  macOS-declared {macos:.0f} {met(m.peak, macos)},"
        + f" idle-process budget (item 16.1) {idle:.0f} {met(m.peak, idle)}"
    )


def _measure_all(store: Store, warm: bool, cold: bool, tag: str) -> list[Measured]:
    results: list[Measured] = []
    gated = not cold
    for case, sample in (
        (c, k) for c in matrix() for k in range(1 if cold else SAMPLES)
    ):
        m = measure(store, case, warm, cold, sample)
        results.append(m)
        limit = ceiling(store.pool)
        verdict = (
            "recorded"
            if not gated
            else (
                "ok"
                if m.peak <= limit
                else (
                    "over (the median decides)"
                    if m.peak <= limit + OUTLIER
                    else "OUTLIER"
                )
            )
        )
        print(_row(f"{store.name}{tag}", m, limit, verdict, store.pool), flush=True)
        if m.hwm:  # Linux: the high-water mark after each request
            print(
                " " * 16 + "after each request: " + " ".join(f"{v:.1f}" for v in m.hwm)
            )
    for (operation, series), peaks in series_peaks(results).items():
        if len(peaks) > 1:
            spread, flat = flatness(peaks)
            verdict = "recorded" if not gated else "ok" if flat else "NOT FLAT"
            medians = " / ".join(f"{peaks[size]:.1f}" for size in sorted(peaks))
            print(
                f"{store.name + tag:<16}flat {operation}, {series}: medians {medians},"
                f" {spread:+.2f} MiB (at most {FLAT:.0f})  {verdict}; design target"
                f" {FLAT_TARGET:.0f} MiB {met(spread, FLAT_TARGET)}"
            )
    return results


def run(store: Store) -> tuple[list[str], dict[str, Any]]:
    with store.serve() as server:
        require_records(_request(server.url, "capabilities", None))
        idle = _peak(server.process)
    print(f"{store.name:<16}{'idle':<43}{'':>20}{idle:>9.1f}{'-':>8}  recorded")
    evidence: dict[str, Any] = {"idle": idle}
    cold = _measure_all(store, warm=False, cold=True, tag="/cold")  # recorded
    evidence["cold"] = [asdict(m) for m in cold]
    results = _measure_all(store, True, False, "/steady")
    failures = verdicts(results, store.pool)
    seals, commits = timing(store)
    for shape, samples in seals.items():
        for size, values in samples.items():
            print(_samples(f"{store.name:<16}seal {shape} {size // MIB} MiB", values))
        value, within = ratio(samples)
        print(
            f"{store.name:<16}seal ratio {shape} 16/1 MiB: {value:.2f}"
            f" (at most {RATIO:.0f})  {'ok' if within else 'OVER RATIO'}"
        )
        if not within:
            failures.append(f"seal {shape}: 16 MiB median is {value:.2f}x the 1 MiB")
    for size, values in commits.items():
        what = f"{store.name:<16}commit {size // MIB} MiB"
        print(_samples(what, values) + "  recorded")
    evidence["results"] = [asdict(m) for m in results]
    evidence["seals"] = {s: {str(k): v for k, v in d.items()} for s, d in seals.items()}
    evidence["commits"] = {str(k): v for k, v in commits.items()}
    return [f"{store.name}: {failure}" for failure in failures], evidence


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/neosian")
    parser.add_argument("--backends", default="file", help="file and/or postgres")
    parser.add_argument("--evidence", type=Path, help="write every sample as JSON")
    args = parser.parse_args(argv)
    backends = [name.strip() for name in args.backends.split(",") if name.strip()]
    if not backends or set(backends) - {"file", "postgres"}:
        parser.error("--backends takes file and/or postgres")
    binary: Path = args.binary.resolve()
    if not binary.is_file():
        parser.error(f"no binary at {binary}: `cargo build --release --locked`")
    dsn = os.environ.get(DSN_ENV, "")
    if "postgres" in backends and not dsn:
        parser.error(f"the postgres backend needs an explicit disposable ${DSN_ENV}")
    if "postgres" in backends:
        try:
            require_pool(POOL_SOURCE.read_text())
        except (OSError, GateFailure) as error:
            print(f"FAILED postgres: {error}")
            return 1
    print(
        f"{'backend':<16}{'operation':<17}{'series':<13}{'size':>16}{'reqs':>5}"
        f"{'items':>7}{'seconds':>8}{'peak MiB':>9}{'ceiling':>8}  verdict"
    )
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for name in backends:
        try:
            if name == "file":
                with tempfile.TemporaryDirectory() as scratch:
                    home = Path(scratch) / "home"
                    home.mkdir()
                    subprocess.run(
                        [str(binary), "records", "enable", "--root", str(home)],
                        check=True,
                        capture_output=True,
                        env=_clean_env(),
                    )
                    backend = Backend(name, ["--root", str(home)], {})
                    found, evidence[name] = run(Store(name, binary, backend, home))
            else:
                found, evidence[name] = run_postgres(binary, dsn)
        except GateFailure as error:
            found = [f"{name}: {error}"]
        failures += found
    if args.evidence:
        args.evidence.write_text(json.dumps(evidence, indent=1))
    for failure in failures:
        print(f"FAILED {failure}")
    return 1 if failures else 0


def run_postgres(binary: Path, dsn: str) -> tuple[list[str], dict[str, Any]]:
    import psycopg

    schema = f"neosian_records_bench_{uuid.uuid4().hex}"
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            for kind in ("postgres", "records-postgres"):
                ddl = subprocess.run(
                    [str(binary), "schema", kind, "--schema", schema],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=_clean_env(),
                ).stdout
                connection.execute(ddl.encode())
        backend = Backend(
            "postgres", ["--schema", schema], {"NEOSIAN_POSTGRES_DSN": dsn}
        )
        store = Store("postgres", binary, backend, dsn=dsn, schema=schema)
        store.pool = POOL_CONNECTIONS
        return run(store)
    finally:
        _drop(dsn, schema)


if __name__ == "__main__":
    sys.exit(main())
