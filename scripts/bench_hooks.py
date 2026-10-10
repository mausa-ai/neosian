"""The latency gate (DESIGN §39.4, ledger #349): the p50 of twenty warm
runs per door and event over one home, the spawn included — the cost a
hook actually pays. Budgets: `version` 5 ms; UserPromptSubmit and
PostToolUse 10 ms; Stop, SessionStart and the MCP `initialize` 50 ms. A
Rust number over budget fails; the Python door is measured, printed and
exempt; an event the binary cannot answer yet reads "not yet". The home
is the maintainer's (`--home`, copied, never written) or a seeded one
of its shape (`--seed`, CI's).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.differential import harness  # noqa: E402
from tests.differential.corpus import MOUNTS, RECORD, SESSION  # noqa: E402
from tests.differential.seed import PROJECT_NAME, REFERENCE, seed  # noqa: E402
from tests.unit.record.payloads import prompt, session_start, stop, tool  # noqa: E402

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
        check=False,
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
    server.stdin.write((json.dumps(_INITIALIZE) + "\n").encode())
    server.stdin.flush()
    server.stdout.readline()
    elapsed = (time.perf_counter() - started) * 1000
    server.kill()
    server.wait()
    return elapsed


def _measure(
    executable: Path,
    event: str,
    argv: tuple[str, ...],
    payload: dict[str, object] | None,
    space: harness.Workspace,
) -> float | None:
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
        if args.home:
            from neosian._foundation.memory.home import project_scope, user_scope

            seeded = base / "seed"
            shutil.copytree(Path(args.home).expanduser(), seeded)
            user, proj = str(user_scope()), str(project_scope(base / PROJECT_NAME))
        else:
            grown = asyncio.run(
                seed(base / "seed", REFERENCE, console=harness.python_door())
            )
            seeded, user, proj = grown.home, grown.user, grown.proj
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
    doors: dict[str, Path] = {"python": harness.python_door(), "rust": rust}
    verbs = harness.binary_verbs(rust)
    failed = False
    with _home(args) as (seeded, user, proj):
        spaces = {
            door: harness.workspace(
                seeded,
                seeded.parent / door,
                user=user,
                proj=proj,
                project_name=PROJECT_NAME,
            )
            for door in doors
        }
        print(
            f"{'event':<18}{'python':>10}{'rust':>10}{'budget':>10}  verdict  (p50 of {RUNS} warm runs, ms, spawn included)"
        )
        for event, argv_, payload, budget in EVENTS:
            python_ms = _measure(
                doors["python"], event, argv_, payload, spaces["python"]
            )
            if argv_[0] in verbs:
                rust_ms = _measure(doors["rust"], event, argv_, payload, spaces["rust"])
                verdict = (
                    "ok" if rust_ms is not None and rust_ms <= budget else "OVER BUDGET"
                )
                failed |= verdict != "ok"
                rust_text = f"{rust_ms:10.1f}"
            else:
                rust_text, verdict = f"{'-':>10}", "not yet"
            print(f"{event:<18}{python_ms:10.1f}{rust_text}{budget:10.1f}  {verdict}")
    print("the Python door is exempt: its floor is the interpreter (§39.1)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
