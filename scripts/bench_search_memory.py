"""Memory gate: peak resident memory of one `neosian serve` request.

Each operation starts a fresh server over an adversarial literal corpus, sends
one request (or, for a size-paged read, every page of it), stops the server
and reads its peak RSS from the operating system. The corpus has large items
(600 turns and 600 documents of 64 KiB) and many items (20,000 turns and
20,000 documents of 1 KiB); the one-letter term "e" makes every one a
candidate. Records are planted as literal format-1 rows (files) or table rows
(Postgres), never written by the binary.

Budgets are MiB per operation and include the server's own startup (about
10 MiB). DESIGN §40.45 explains how they were chosen; the two spread corpora
(100,000 candidates named by 128-character conversation ids or 500-character
paths) have budgets of their own, set from measurement (§40.46, #417). Since S14 (§40.46)
answers stream, so answer-sized requests (limit 500 turns of 64 KiB, a 32 MiB
answer; a 500-turn page of turns) are gated too, as are reads that follow
size pages to the end of a large conversation.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
TOKEN: Final = "bench-search-memory"
DSN_ENV: Final = "NEOSIAN_TEST_POSTGRES_DSN"
BUDGET: Final = 32.0
SINCE: Final = "2000-01-01T00:00:00Z"
STAMP: Final = "2026-01-01T00:00:00Z"
ACTOR: Final = "cli:bench"
# (items, bytes per item)
LARGE: Final = (600, 64 * 1024)
MANY: Final = (20_000, 1024)
HISTORY_LARGE: Final = "bench-large"
HISTORY_MANY: Final = "bench-many"
SCOPE_LARGE: Final = "user:bench-large"
SCOPE_MANY: Final = "user:bench-many"
# Facts at the default candidate bound (100,000): each candidate its own
# conversation with a 128-character id, and documents with 500-character
# paths, the two records that grow with what a candidate is called (#417).
SPREAD: Final = 100_000
SPREAD_TERM: Final = "zq"
SCOPE_PATHS: Final = "user:bench-paths"
PATH_PREFIX: Final = "/".join(("a" * 120, "b" * 120, "c" * 120, "d" * 128))
WORDS: Final = (
    "deploy",
    "review",
    "latency",
    "kernel",
    "memory",
    "agent",
    "context",
    "session",
    "storage",
    "network",
    "release",
    "measure",
    "editor",
    "pipeline",
    "cluster",
    "replica",
    "gateway",
    "theorem",
    "element",
)


@dataclass(frozen=True)
class Operation:
    name: str
    route: str
    body: dict[str, Any]
    budget: float | None  # MiB; None records without gating
    page_bytes: int | None = None  # follow size pages of this ceiling


def _history(
    conversation: str, order: str | None, limit: int, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": "e", "conversations": [conversation]}
    body.update(limit=limit, **extra)
    if order is not None:
        body["order"] = order
    return body


def _memory(scope: str, order: str, passages: int) -> dict[str, Any]:
    body = {"scope": scope, "query": "e", "order": order, "limit": 100}
    return {**body, "passages": passages}


HISTORY: Final = "conversation/search_turns"
MEMORY: Final = "memory/search"
# Budgets for the two spread corpora (DESIGN §40.46, #417): the larger
# backend's measured peak plus 30% for allocator and platform variance,
# rounded up to 8 MiB. Measured 2026-11-17 on macOS: history 32.7 MiB on
# files and 42.6 MiB on Postgres (about 230 and 330 bytes per candidate:
# facts plus one copy of each 128-character id), memory 66.1 and 66.7 MiB
# (about 580 bytes per candidate: facts plus the 500-character path).
SPREAD_HISTORY: Final = 56.0
SPREAD_MEMORY: Final = 88.0
TURNS: Final = "conversation/read_turns"
SMALL_PAGE: Final = 64 * 1024
CLIENT_PAGE: Final = 1 << 20
OPERATIONS: Final = (
    Operation(
        "history relevance",
        HISTORY,
        _history(HISTORY_LARGE, "relevance", 20),
        BUDGET,
    ),
    Operation("history newest", HISTORY, _history(HISTORY_LARGE, "newest", 20), BUDGET),
    Operation(
        "history newest since",
        HISTORY,
        _history(HISTORY_LARGE, "newest", 20, since=SINCE),
        BUDGET,
    ),
    Operation("history version 1", HISTORY, _history(HISTORY_LARGE, None, 20), BUDGET),
    Operation(
        "history relevance many",
        HISTORY,
        _history(HISTORY_MANY, "relevance", 20),
        BUDGET,
    ),
    Operation(
        "history version 1 many", HISTORY, _history(HISTORY_MANY, None, 20), BUDGET
    ),
    Operation("memory relevance", MEMORY, _memory(SCOPE_LARGE, "relevance", 0), BUDGET),
    Operation(
        "memory relevance passages",
        MEMORY,
        _memory(SCOPE_LARGE, "relevance", 3),
        BUDGET,
    ),
    Operation("memory newest", MEMORY, _memory(SCOPE_LARGE, "newest", 0), BUDGET),
    Operation(
        "memory newest passages", MEMORY, _memory(SCOPE_LARGE, "newest", 3), BUDGET
    ),
    Operation("memory path", MEMORY, _memory(SCOPE_LARGE, "path", 0), BUDGET),
    Operation("memory path passages", MEMORY, _memory(SCOPE_LARGE, "path", 3), BUDGET),
    Operation(
        "memory relevance many", MEMORY, _memory(SCOPE_MANY, "relevance", 3), BUDGET
    ),
    Operation("memory newest many", MEMORY, _memory(SCOPE_MANY, "newest", 3), BUDGET),
    Operation(
        "history relevance, 500",
        HISTORY,
        _history(HISTORY_LARGE, "relevance", 500),
        BUDGET,
    ),
    Operation(
        "history version 1, 500", HISTORY, _history(HISTORY_LARGE, None, 500), BUDGET
    ),
    Operation("turns read, 500", TURNS, {"conversation_id": HISTORY_LARGE}, BUDGET),
    Operation(
        "history relevance, 100k conversations",
        HISTORY,
        {"query": SPREAD_TERM, "order": "relevance", "limit": 20},
        SPREAD_HISTORY,
    ),
    Operation(
        "memory relevance, 500-char paths",
        MEMORY,
        _memory(SCOPE_PATHS, "relevance", 0),
        SPREAD_MEMORY,
    ),
    Operation(
        "history version 1, 500, paged",
        HISTORY,
        _history(HISTORY_LARGE, None, 500),
        BUDGET,
        CLIENT_PAGE,
    ),
    Operation(
        "turns read, paged",
        TURNS,
        {"conversation_id": HISTORY_LARGE},
        BUDGET,
        SMALL_PAGE,
    ),
    Operation(
        "turns read many, paged",
        TURNS,
        {"conversation_id": HISTORY_MANY},
        BUDGET,
        SMALL_PAGE,
    ),
)


def _text(seed: int, size: int) -> str:
    rng = random.Random(seed)
    parts: list[str] = []
    length = 0
    while length < size:
        word = f"{rng.choice(WORDS)}{rng.randrange(1000)}"
        parts.append(word)
        length += len(word) + 1
    return " ".join(parts)[:size]


def _message(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }


def _items() -> Iterator[tuple[bool, int, str]]:
    """(large, number, text) for every planted item of one kind."""
    for seed, (large, (count, size)) in enumerate([(True, LARGE), (False, MANY)]):
        for number in range(1, count + 1):
            yield large, number, _text(seed * 1_000_000 + number, size)


def _spread_id(number: int) -> str:
    return f"{number:08d}" + "x" * 120


def _spread_message(number: int) -> dict[str, Any]:
    return _message(f"spread {SPREAD_TERM} note {number}")


def _spread_path(number: int) -> str:
    # Segments are at most 128 characters; the path is 500.
    return f"{PATH_PREFIX}/{number:010d}"


def _plant_files(home: Path) -> None:
    for conversation in (HISTORY_LARGE, HISTORY_MANY):
        (home / "conversations" / conversation).mkdir(parents=True)
    logs = {
        large: (home / "conversations" / name / "turns.jsonl").open(
            "w", encoding="utf-8"
        )
        for large, name in ((True, HISTORY_LARGE), (False, HISTORY_MANY))
    }
    try:
        for large, number, text in _items():
            row = {
                "neosian_format": 1,
                "turn": number,
                "created_at": STAMP,
                "actor": ACTOR,
                "messages": [_message(text)],
            }
            logs[large].write(json.dumps(row) + "\n")
    finally:
        for log in logs.values():
            log.close()
    header = (
        f"---\nneosian_format: 1\nversion: 1\ncreated_at: '{STAMP}'\n"
        f"updated_at: '{STAMP}'\nactor: {ACTOR}\n---\n"
    )
    for large, number, text in _items():
        scope = SCOPE_LARGE if large else SCOPE_MANY
        folder = home / scope.replace(":", "%3A") / "documents" / "notes"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{number:05d}.md").write_text(header + text, encoding="utf-8")
    conversations = home / "conversations"
    for number in range(SPREAD):
        folder = conversations / _spread_id(number)
        folder.mkdir()
        row = {
            "neosian_format": 1,
            "turn": 1,
            "created_at": STAMP,
            "actor": ACTOR,
            "messages": [_spread_message(number)],
        }
        (folder / "turns.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    documents = home / SCOPE_PATHS.replace(":", "%3A") / "documents"
    (documents / PATH_PREFIX).mkdir(parents=True)
    for number in range(SPREAD):
        path = documents / f"{_spread_path(number)}.md"
        path.write_text(header + f"note {number} here", encoding="utf-8")


def _plant_postgres(binary: Path, dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    ddl = subprocess.run(
        [str(binary), "schema", "postgres", "--schema", schema],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    name = sql.Identifier(schema)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(ddl.encode())
        insert = sql.SQL(
            "INSERT INTO {}.conversations (conversation_id, created_at) VALUES (%s, %s)"
        ).format(name)
        for conversation in (HISTORY_LARGE, HISTORY_MANY):
            connection.execute(insert, (conversation, STAMP))
        with connection.cursor() as cursor:
            cursor.executemany(
                sql.SQL(
                    "INSERT INTO {}.turns (conversation_id, turn, messages, created_at,"
                    " neosian_format, actor, search_text) VALUES (%s, %s, %s, %s, 1,"
                    " %s, %s)"
                ).format(name),
                (
                    (
                        HISTORY_LARGE if large else HISTORY_MANY,
                        number,
                        json.dumps([_message(text)]),
                        STAMP,
                        ACTOR,
                        # A single text message's searchable text is its content.
                        text,
                    )
                    for large, number, text in _items()
                ),
            )
            cursor.executemany(insert, ((_spread_id(n), STAMP) for n in range(SPREAD)))
            cursor.executemany(
                sql.SQL(
                    "INSERT INTO {}.turns (conversation_id, turn, messages, created_at,"
                    " neosian_format, actor, search_text) VALUES (%s, 1, %s, %s, 1,"
                    " %s, %s)"
                ).format(name),
                (
                    (
                        _spread_id(n),
                        json.dumps([_spread_message(n)]),
                        STAMP,
                        ACTOR,
                        _spread_message(n)["content"],
                    )
                    for n in range(SPREAD)
                ),
            )
            cursor.executemany(
                sql.SQL(
                    "INSERT INTO {}.memories (scope, path, content, version, created_at,"
                    " updated_at, actor) VALUES (%s, %s, %s, 1, %s, %s, %s)"
                ).format(name),
                (
                    (
                        SCOPE_PATHS,
                        _spread_path(n),
                        f"note {n} here",
                        STAMP,
                        STAMP,
                        ACTOR,
                    )
                    for n in range(SPREAD)
                ),
            )
            cursor.executemany(
                sql.SQL(
                    "INSERT INTO {}.memories (scope, path, content, version, created_at,"
                    " updated_at, actor) VALUES (%s, %s, %s, 1, %s, %s, %s)"
                ).format(name),
                (
                    (
                        SCOPE_LARGE if large else SCOPE_MANY,
                        f"notes/{number:05d}",
                        text,
                        STAMP,
                        STAMP,
                        ACTOR,
                    )
                    for large, number, text in _items()
                ),
            )


def _drop(dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _call(url: str, route: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        answer: dict[str, Any] = json.loads(response.read())
        return answer


def _count(answer: dict[str, Any]) -> int:
    return len(answer.get("turns", answer.get("hits", [])))


def _follow(url: str, operation: Operation) -> tuple[int, int]:
    """(items, pages): one request, or every size page of one read."""
    if operation.page_bytes is None:
        return _count(_call(url, operation.route, operation.body)), 1
    body = {**operation.body, "page_bytes": operation.page_bytes}
    items = pages = 0
    while True:
        answer = _call(url, operation.route, body)
        items, pages = items + _count(answer), pages + 1
        if answer["continuation"] is None:
            return items, pages
        body["continuation"] = answer["continuation"]


@dataclass
class Backend:
    name: str
    args: list[str]
    env: dict[str, str]


@contextmanager
def _server(
    binary: Path, backend: Backend
) -> Iterator[tuple[str, subprocess.Popen[bytes]]]:
    port = _port()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")},
        **backend.env,
        "NEOSIAN_SERVE_TOKEN": TOKEN,
    }
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [str(binary), "serve", "--port", str(port), *backend.args],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(400):
                try:
                    urllib.request.urlopen(f"{url}/health", timeout=1).read()
                    break
                except (urllib.error.URLError, ConnectionError):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(f"serve exited: {log.read()!r}") from None
                    time.sleep(0.05)
            yield url, process
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=30)


def _peak(process: subprocess.Popen[bytes]) -> float:
    """Stop the server and read its peak resident set size in MiB.

    Linux reports the server's own high-water mark in /proc (a child's
    `ru_maxrss` there also counts the parent's size at fork); macOS reports
    the child's peak in bytes when it is reaped.
    """
    status = Path(f"/proc/{process.pid}/status")
    high = None
    if status.is_file():
        for line in status.read_text().splitlines():
            if line.startswith("VmHWM:"):
                high = int(line.split()[1]) * 1024
    process.send_signal(signal.SIGTERM)
    _, _, usage = os.wait4(process.pid, 0)
    process.returncode = 0
    if high is None:
        if sys.platform != "darwin":
            raise RuntimeError("no peak resident size for this platform")
        high = usage.ru_maxrss
    return high / (1 << 20)


def _run(binary: Path, backend: Backend) -> bool:
    failed = False
    for operation in OPERATIONS:
        with _server(binary, backend) as (url, process):
            started = time.perf_counter()
            hits, pages = _follow(url, operation)
            seconds = time.perf_counter() - started
            peak = _peak(process)
        if operation.budget is None:
            verdict, budget = "recorded", "-"
        else:
            verdict = "ok" if peak <= operation.budget else "OVER BUDGET"
            budget = f"{operation.budget:.0f}"
            failed |= verdict != "ok"
        print(
            f"{backend.name:<9}{operation.name:<32}{hits:>6}{pages:>6}{seconds:>8.2f}"
            f"{peak:>9.1f}{budget:>8}  {verdict}",
            flush=True,
        )
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--binary",
        type=Path,
        default=ROOT / "target/release/neosian",
        help="the release binary under test (default: target/release/neosian)",
    )
    parser.add_argument(
        "--backends",
        default="file",
        help="comma-separated: file, postgres (postgres requires an explicit "
        f"disposable ${DSN_ENV})",
    )
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
    print(
        f"{'backend':<9}{'operation':<32}{'items':>6}{'pages':>6}{'seconds':>8}"
        f"{'peak MiB':>9}{'budget':>8}  verdict"
    )
    failed = False
    for name in backends:
        if name == "file":
            with tempfile.TemporaryDirectory() as scratch:
                home = Path(scratch) / "home"
                home.mkdir()
                _plant_files(home)
                failed |= _run(binary, Backend("file", ["--root", str(home)], {}))
            continue
        schema = f"neosian_bench_{uuid.uuid4().hex}"
        try:
            _plant_postgres(binary, dsn, schema)
            env = {"NEOSIAN_POSTGRES_DSN": dsn}
            failed |= _run(binary, Backend("postgres", ["--schema", schema], env))
        finally:
            _drop(dsn, schema)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
