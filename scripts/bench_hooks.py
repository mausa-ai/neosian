"""Latency gate: p50 of 20 warm binary runs, spawn included, over a 50 MB home.

Budgets: version 5 ms; prompt/tool 10 ms; stop/start/MCP initialize 50 ms.
A supplied --home is copied, never changed. The default seed is literal data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.contract.replay import harness  # noqa: E402
from tests.contract.replay.corpus import MOUNTS, RECORD, SESSION  # noqa: E402

PROJECT_NAME = "demo"
from tests.contract.replay.payloads import (  # noqa: E402
    prompt,
    session_start,
    stop,
    tool,
)

RUNS: Final = 20
WARMUP: Final = 3
# event -> (argv, payload, budget in ms); None payload is a bare verb.
EVENTS: Final = (
    ("version", ("version",), None, 5.0),
    ("UserPromptSubmit", RECORD, prompt("bench", session=SESSION), 10.0),
    ("PostToolUse", RECORD, tool(session=SESSION), 10.0),
    ("Stop", RECORD, stop("bench", session=SESSION), 50.0),
    ("SessionStart", RECORD, session_start("startup", session=SESSION), 50.0),
    ("initialize", ("mcp", *MOUNTS), None, 50.0),
)
_INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "bench", "version": "0"},
    },
}


def _one_shot(argv: list[str], stdin: bytes | None, space: harness.Workspace) -> float:
    started = time.perf_counter()
    subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        env=space.environment(),
        cwd=space.project,
        check=True,
        timeout=30,
    )
    return (time.perf_counter() - started) * 1000


def _initialize(argv: list[str], space: harness.Workspace) -> float:
    started = time.perf_counter()
    server = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=space.environment(),
        cwd=space.project,
    )
    assert server.stdin is not None and server.stdout is not None
    try:
        server.stdin.write((json.dumps(_INITIALIZE) + "\n").encode())
        server.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as reader:
            response = reader.submit(server.stdout.readline)
            try:
                line = response.result(timeout=30)
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                server.kill()
            assert json.loads(line)["result"]["serverInfo"]["name"] == "neosian-memory"
        return elapsed
    finally:
        if server.poll() is None:
            server.kill()
        server.wait(timeout=5)


def _measure(
    executable: Path,
    event: str,
    argv: tuple[str, ...],
    payload: dict[str, object] | None,
    space: harness.Workspace,
) -> float:
    full = [str(executable), *(space.expand(a) for a in argv)]
    stdin = None if payload is None else space.expand(json.dumps(payload)).encode()

    def once() -> float:
        if event == "initialize":
            return _initialize(full, space)
        if (
            event == "Stop"
        ):  # a real landing: a prompt and a tool round first, unmeasured
            for before in (prompt("bench", session=SESSION), tool(session=SESSION)):
                _one_shot(full, space.expand(json.dumps(before)).encode(), space)
        return _one_shot(full, stdin, space)

    for _ in range(WARMUP):
        once()
    return statistics.median(once() for _ in range(RUNS))


@contextmanager
def _home(args: argparse.Namespace) -> Iterator[tuple[Path, str, str]]:
    """The reference home: a copy of the maintainer's, or a seeded one."""
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch)
        source = ROOT / "tests/contract/replay/home"
        metadata = json.loads((source.parent / "seed.json").read_text())
        seeded = base / "seed"
        shutil.copytree(Path(args.home).expanduser() if args.home else source, seeded)
        user, proj = metadata["user"], metadata["proj"]
        if not args.home:
            # Literal records, independent of a product implementation: 50 MB
            # across 50 conversations, four 250 KB turns each.
            for n in range(50):
                directory = seeded / "conversations" / f"bench-{n:03d}"
                directory.mkdir(parents=True)
                turns = [
                    {
                        "neosian_format": 1,
                        "turn": turn,
                        "created_at": "2026-01-01T00:00:00Z",
                        "actor": "cli:bench",
                        "messages": [
                            {"role": "user", "content": "bench " + "x" * 250_000}
                        ],
                    }
                    for turn in range(1, 5)
                ]
                (directory / "turns.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in turns)
                )
        yield seeded, user, proj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--home", help="a home to copy and measure against (never written)"
    )
    parser.add_argument(
        "--seed", action="store_true", help="seed a home of the reference shape instead"
    )
    parser.add_argument(
        "--binary",
        type=Path,
        help=f"the Rust binary (default: ${harness.BINARY_ENV}, else target/)",
    )
    args = parser.parse_args(argv)
    if bool(args.home) == args.seed:
        parser.error("one of --home DIR or --seed")
    rust = args.binary or harness.rust_door()
    if rust is None:
        parser.error("no binary: `cargo build --release` or --binary PATH")
    rust = rust.resolve()
    doors: dict[str, Path] = {"rust": rust}
    verbs = harness.binary_verbs(rust)
    failed = False
    with _home(args) as (seeded, user, proj):
        spaces = {
            door: harness.workspace(
                seeded,
                seeded.parent / door,
                user=user,
                proj=proj,
                session="",  # the gate's cases name no session
                project_name=PROJECT_NAME,
            )
            for door in doors
        }
        print(
            f"{'event':<18}{'rust':>10}{'budget':>10}  verdict  (p50 of {RUNS} warm runs, ms, spawn included)"
        )
        for event, argv_, payload, budget in EVENTS:
            if argv_[0] in verbs:
                rust_ms = _measure(doors["rust"], event, argv_, payload, spaces["rust"])
                verdict = (
                    "ok" if rust_ms is not None and rust_ms <= budget else "OVER BUDGET"
                )
                failed |= verdict != "ok"
                rust_text = f"{rust_ms:10.1f}"
            else:
                raise RuntimeError(f"required benchmark verb missing: {argv_[0]}")
            print(f"{event:<18}{rust_text}{budget:10.1f}  {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
