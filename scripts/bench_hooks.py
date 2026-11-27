"""Latency gate: p50 of 20 warm binary runs, spawn included, over a 50 MB home.

Budgets: version 5 ms; prompt/tool 10 ms; stop/start/MCP initialize 50 ms.
A supplied --home is copied, never changed. The default seed is literal data.

The hook events are measured again on three more copies of the home (DESIGN
section 40.54 item 17; plan section C, rulings D12 and E13 of the T1e1 plan),
under the same budgets: with a pending queue of 50 conversations of four
1 MiB turns for a URL no hook here serves, every conversation lock held by
the bench so each lander a hook starts exits at once, every append scanning
it against the default keep limit; with `[record] keep_bytes` written out,
and once at the limit,
so every append deletes a turn; and on a home that keeps records, where a
Stop never lands inline. Every lander a hook starts is stopped by the bench.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
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
# The queue cases: conversations, turns each, a turn's bytes; the design's
# 512 MiB default (item 23) as the limit every append scans against.
QUEUE: Final = (50, 4, 1_048_576)
FULL_KEEP: Final = 536_870_912
UNSERVED: Final = "http://127.0.0.1:9"
SLOTS: Final = ("slot-1.lock", "slot-2.lock")
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
            assert json.loads(line)["result"]["serverInfo"]["name"] == "neosian"
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
    prepare: Callable[[], None] | None = None,
) -> float:
    full = [str(executable), *(space.expand(a) for a in argv)]
    stdin = None if payload is None else space.expand(json.dumps(payload)).encode()

    def once() -> float:
        if prepare is not None:  # unmeasured
            prepare()
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


def _turn_text(size: int, nonce: int) -> str:
    """A closed turn of `size` bytes: its opening line, a prompt, a stop."""
    opening = json.dumps(
        {
            "kind": "span",
            "opened_at": "2026-01-01T00:00:00.000000Z",
            "nonce": f"00000000-0000-4000-8000-{nonce:012d}",
        }
    )
    stop = json.dumps({"kind": "stop", "text": "done"})
    head = f'{opening}\n{{"kind": "prompt", "text": "'
    tail = f'"}}\n{stop}\n'
    return head + "x" * (size - len(head) - len(tail)) + tail


def _plant_turn(
    home: Path, conversation: str, n: int, closed_at: str, size: int
) -> None:
    """A pending turn and its state, for `UNSERVED`, as a Stop closes one."""
    directory = home / "spool" / "pending" / conversation
    directory.mkdir(parents=True, exist_ok=True)
    text = _turn_text(size, n)
    state = {
        "draft": "p" + f"{n:032x}",
        "next_seq": 0,
        "fingerprint": None,
        "destination": {
            "kind": "remote",
            "url": UNSERVED,
            "credential_sha256": "0" * 64,
        },
        "attempt": 0,
        "closed_at": closed_at,
        "bytes": len(text.encode()),
        "agent": "claude-code",
        "actor": f"claude-code:{conversation}",
        "session": conversation,
        "conversation_id": conversation,
        "scope": "user:bench",
        "mount": "user",
        "provenance": None,
        "parent": None,
        "project": "/bench",
    }
    (directory / f"{n}.state").write_text(json.dumps(state))
    (directory / f"{n}.jsonl").write_text(text)


def _plant_queue(home: Path) -> list[str]:
    """The pending queue; the conversations sort before the bench's own."""
    conversations, turns, size = QUEUE
    names = [f"0bench-{c:03d}" for c in range(conversations)]
    for c, name in enumerate(names):
        for n in range(1, turns + 1):
            _plant_turn(home, name, n, f"2026-01-01T00:{c:02d}:{n:02d}.000000Z", size)
    return names


def _kept_bytes(home: Path) -> int:
    """What the keep limit counts: every `.jsonl` of the home's spool trees."""
    total = 0
    for tree in (home / "spool", home / "profile-spool"):
        for pattern in ("*.jsonl", "pending/*/*.jsonl", "held/*/*.jsonl"):
            total += sum(file.stat().st_size for file in tree.glob(pattern))
    return total


def _keep(home: Path, keep: int | None) -> None:
    config = home / "config.toml"
    if keep is None:
        config.unlink(missing_ok=True)
    else:
        config.write_text(f"[record]\nkeep_bytes = {keep}\n")


@contextmanager
def _held(files: list[Path]) -> Iterator[None]:
    """Each file locked with `flock`, as the binary's `try_lock` takes it."""
    import fcntl

    with ExitStack() as stack:
        for file in files:
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                file.write_bytes(b"\0")
            handle = stack.enter_context(file.open("rb"))
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _locks(home: Path, conversations: list[str]) -> list[Path]:
    return [home / "spool" / "pending" / c / ".lock" for c in conversations]


def _slots(home: Path) -> list[Path]:
    return [home / "spool" / "landing" / name for name in SLOTS]


def _landers(home: Path) -> set[int]:
    """The `record-land` processes started for `home`, from the process table."""
    table = subprocess.run(
        ["ps", "-A", "-o", "pid=", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # The home as a whole argument: another home sharing its prefix is not it.
    lander = re.compile(rf"record-land --home {re.escape(str(home))}(\s|$)")
    return {
        int(line.split(None, 1)[0])
        for line in table.splitlines()
        if lander.search(line)
    }


def _stop_landers(home: Path) -> int:
    """Kills every lander the bench's hooks started for `home`; how many."""
    stopped: set[int] = set()
    deadline = time.monotonic() + 10
    while running := _landers(home):
        for pid in running:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            stopped.add(pid)
        if time.monotonic() > deadline:
            raise RuntimeError(f"landers still running: {sorted(running)}")
        time.sleep(0.05)
    return len(stopped)


def _row(label: str, ms: float, budget: float) -> bool:
    """Prints one case; whether it is over its budget."""
    verdict = "ok" if ms <= budget else "OVER BUDGET"
    print(f"{label:<40}{ms:10.1f}{budget:10.1f}  {verdict}")
    return verdict != "ok"


def _hook(event: str) -> tuple[dict[str, object] | None, float]:
    """The payload and budget of a hook event of `EVENTS`."""
    for name, _, payload, budget in EVENTS:
        if name == event:
            return payload, budget
    raise KeyError(event)


def _case(
    rust: Path,
    space: harness.Workspace,
    label: str,
    event: str,
    prepare: Callable[[], None] | None = None,
) -> bool:
    payload, budget = _hook(event)
    return _row(label, _measure(rust, event, RECORD, payload, space, prepare), budget)


def _queue_cases(rust: Path, seeded: Path, user: str, proj: str) -> tuple[bool, Path]:
    """The pending queue with `keep_bytes` unset, then set (D12)."""
    space = harness.workspace(
        seeded,
        seeded.parent / "queue",
        user=user,
        proj=proj,
        session="",
        project_name=PROJECT_NAME,
    )
    home = space.home
    names = _plant_queue(home)
    conversations, turns, size = QUEUE
    over = False
    try:
        with _held(_locks(home, [*names, SESSION])):
            megabytes = _kept_bytes(home) / 1e6
            print(
                f"pending queue: {conversations} conversations x {turns} turns "
                f"({megabytes:.1f} MB), every lock held, keep_bytes unset (512 MiB)"
            )
            over |= _case(rust, space, "Stop, slots free (spawn)", "Stop")
            with _held(_slots(home)):
                over |= _case(rust, space, "Stop, both slots held", "Stop")
            over |= _case(rust, space, "UserPromptSubmit (spawn)", "UserPromptSubmit")
            over |= _case(rust, space, "PostToolUse", "PostToolUse")
            over |= _case(
                rust, space, "SessionStart (state scan, spawn)", "SessionStart"
            )
            _keep(home, FULL_KEEP)
            print(f"full queue: the same, keep_bytes = {FULL_KEEP}, every append scans")
            over |= _case(
                rust, space, "UserPromptSubmit (scan, spawn)", "UserPromptSubmit"
            )
            over |= _case(rust, space, "PostToolUse (scan)", "PostToolUse")
            over |= _case(rust, space, "Stop (scan, spawn)", "Stop")
            # At the limit: an older turn of an unlocked conversation is
            # planted before each run, and that run deletes and counts it.
            _keep(home, _kept_bytes(home) + size // 2)

            def older() -> None:
                closed = "2025-01-01T00:00:00.000000Z"
                _plant_turn(home, "0bench-old", 1, closed, size)

            over |= _case(
                rust,
                space,
                "PostToolUse at the limit (deletes one)",
                "PostToolUse",
                older,
            )
            drops = json.loads((home / "spool/landing/drops.json").read_text())
            assert drops["turns"] == WARMUP + RUNS, drops
            assert drops["bytes"] == (WARMUP + RUNS) * size, drops
    finally:
        print(f"landers stopped: {_stop_landers(home)}")
    return over, home


def _records_cases(rust: Path, seeded: Path, user: str, proj: str) -> tuple[bool, Path]:
    """A home that keeps records: a Stop starts a lander (ruling E13)."""
    space = harness.workspace(
        seeded,
        seeded.parent / "records",
        user=user,
        proj=proj,
        session="",
        project_name=PROJECT_NAME,
    )
    enable = [str(rust), "records", "enable", "--root", str(space.home), "--json"]
    subprocess.run(enable, env=space.environment(), capture_output=True, check=True)
    print("records home: the same home after `neosian records enable`")
    over = False
    try:
        for event in ("UserPromptSubmit", "PostToolUse", "Stop", "SessionStart"):
            over |= _case(rust, space, event, event)
    finally:
        print(f"landers stopped: {_stop_landers(space.home)}")
    return over, space.home


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
            f"{'case':<40}{'rust':>10}{'budget':>10}  verdict  (p50 of {RUNS} warm runs, ms, spawn included)"
        )
        print("plain home")
        for event, argv_, payload, budget in EVENTS:
            if argv_[0] in verbs:
                rust_ms = _measure(doors["rust"], event, argv_, payload, spaces["rust"])
                failed |= _row(event, rust_ms, budget)
            else:
                raise RuntimeError(f"required benchmark verb missing: {argv_[0]}")
        homes = [spaces["rust"].home]
        print(f"landers stopped: {_stop_landers(homes[0])}")
        for group in (_queue_cases, _records_cases):
            over, home = group(rust, seeded, user, proj)
            failed |= over
            homes.append(home)
        left = sum(len(_landers(home)) for home in homes)
        print(f"landers left running: {left}")
        failed |= left != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
