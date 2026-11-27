"""Records proof: the release image completes the records workload in 192 MiB.

DESIGN §40.54 item 16.4 and the T1gc slice of the T1f plan. The image that
`make test-container` builds serves a store that keeps records from a
container limited to 192 MiB with swap disabled (`--memory 192m
--memory-swap 192m`): first a FileStore on a volume after `neosian records
enable`, then PostgreSQL outside the container with schema 4 and records
extension 1 in a disposable schema. Over HTTP only:

1. `neosian import DIR --url` of the one-tenth synthetic home
   (`tests/contract/records/synthetic.py`), run by the same image in a
   second container without a limit that shares the server's network. Then
   the import's units against the archive, the archive unchanged, the
   listing, every conversation's full view record by record with the unit
   digest its restore committed, and `read_turns` against the archive's
   bytes (the cheap checks of the T1gb differential; the twin store and the
   trails stay in `make test` and `make test-postgres`).
2. The operations of the records memory gate at their largest sizes
   (`scripts/bench_records_memory.py`): write parts and seal of a 16 MiB
   turn in both shapes, a part of 2,048 tiny records, the piece fetch of the
   16 MiB result, restore parts and commit of the 16 MiB turns and of the
   64 MiB conversation, its full and light reads and its newest and
   relevance searches, and a sweep preview of a 16 MiB stale draft, each
   answer read back.

The proof fails when the container lacks the 192 MiB limit or has swap
beside it (its configuration and the limit the runtime reports), when it is
OOM-killed, restarted or stopped before the work ends, when an operation is
refused or does not read back, when the server does not shut down
gracefully, or when a leg that should run did not. The container's state is
inspected before it is removed. Its peak is reported where the runtime
exposes it: `docker stats` sampled once a second during the work, and, when
the docker host's files can be read (here, or through the command prefix in
`NEOSIAN_CONTAINER_HOST_SHELL`, such as `limactl shell docker --`), the
cgroup's `memory.peak` (page cache included, which the kernel reclaims),
its `oom_kill` count and the server's own `VmHWM`.

`--native BINARY` is a dry run of the same workload against a native
`BINARY serve` without any limit, to check the workload's logic where the
image cannot be built; it is not the proof and says so.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bench_records_memory import (  # noqa: E402
    SWEEP,
    WRITES,
    Case,
    GateFailure,
    Store,
    conversation_of,
    drive,
    expect,
    label,
    matrix,
    require_records,
    turns_of,
    write,
)
from scripts.bench_search_memory import DSN_ENV, Backend, _peak, _port  # noqa: E402
from scripts.container_schema import create, drop  # noqa: E402
from tests.contract.records.differential import (  # noqa: E402
    EVERY_KIND,
    Tally,
    conversations,
    differ,
    full_view,
    post_raw,
    tree_digest,
    turns,
    units,
)
from tests.contract.records.importing import restore_id  # noqa: E402
from tests.contract.records.restoring import RESTORE  # noqa: E402
from tests.contract.records.support import compact  # noqa: E402
from tests.contract.records.synthetic import write_home  # noqa: E402
from tests.contract.support import ADMIN  # noqa: E402

MIB: Final = 1 << 20
LIMIT: Final = 192 * MIB  # the container's memory, and memory plus swap
LIMIT_OPTIONS: Final = ("--memory", "192m", "--memory-swap", "192m")
TOKEN: Final = ADMIN  # the differential's reads send it
ENTRYPOINT: Final = "/usr/local/bin/neosian"
INNER_PORT: Final = 6367
BOOT_TIMEOUT: Final = 30
STOP_TIMEOUT: Final = 30
SHUTDOWN: Final = "Application shutdown complete"
SAMPLE_SECONDS: Final = 1.0
STALE: Final = "stale0"  # the sweep's draft, as `drive` counts it (sample 0)
HOST_SHELL: Final = "NEOSIAN_CONTAINER_HOST_SHELL"
UNITS: Final = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30}
UNITS_SI: Final = {"kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9}
PROBE: Final = (
    "d=$(find /sys/fs/cgroup -maxdepth 9 -type d \\( -name 'docker-{id}.scope'"
    " -o -name '{id}' \\) 2>/dev/null | head -n 1); [ -n \"$d\" ] || exit 3;"
    ' echo "peak $(cat "$d/memory.peak" 2>/dev/null)"; cat "$d/memory.events";'
    ' for p in $(cat "$d/cgroup.procs"); do sed -n "s/^VmHWM:/hwm/p"'
    ' "/proc/$p/status"; done'
)
Importer = Callable[[], list[dict[str, Any]]]


class ProofFailure(Exception):
    """The container or the workload did not do what the proof requires."""


def largest_cases() -> list[Case]:
    """The memory gate's cases at the largest size of each series, in the
    gate's order (a write before its seal, a restore before its commit)."""
    top: dict[tuple[str, str], int] = {}
    for case in matrix():
        key = (case.operation, case.series)
        top[key] = max(top.get(key, 0), case.size)
    return [c for c in matrix() if c.size == top[(c.operation, c.series)]]


def plan(served: str, dsn: str) -> tuple[list[str], list[str]]:
    """(the legs to run, the legs skipped with why): files always,
    PostgreSQL when the image serves it and a test DSN is set."""
    if "postgres" not in {name.strip() for name in served.split(",")}:
        return ["file"], [f"postgres (the image serves: {served})"]
    if not dsn:
        return ["file"], [f"postgres ({DSN_ENV} not set)"]
    return ["file", "postgres"], []


def unfinished(planned: list[str], completed: list[str]) -> list[str]:
    return [f"the {leg} leg did not run" for leg in planned if leg not in completed]


def container_dsn(dsn: str, override: str = "") -> str:
    """The DSN the container reaches: `override`, or the host's loopback
    renamed `host.docker.internal`; a user, password or database spelled
    "localhost" stays as written."""
    pattern = r"^([a-z]+://([^@/]*@)?)(localhost|127\.0\.0\.1)([:/?]|$)"
    return override or re.sub(pattern, r"\1host.docker.internal\4", dsn)


def parse_size(text: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", text)
    scale = {**UNITS, **UNITS_SI}.get(match.group(2)) if match else None
    if match is None or scale is None:
        raise ValueError(f"not a size: {text!r}")
    return round(float(match.group(1)) * scale)


def parse_usage(text: str) -> tuple[int, int] | None:
    """`docker stats`' MemUsage, `45.3MiB / 192MiB`: (usage, limit)."""
    used, slash, limit = text.partition("/")
    try:
        return (parse_size(used), parse_size(limit)) if slash else None
    except ValueError:
        return None


def limit_failures(host: dict[str, Any], reported: int | None) -> list[str]:
    """The configured limit, swap disabled beside it, and the limit the
    runtime reports, which is absent where the kernel discarded it."""
    failures = []
    if host.get("Memory") != LIMIT:
        failures.append(f"configured memory limit {host.get('Memory')}, not {LIMIT}")
    if host.get("MemorySwap") != LIMIT:
        failures.append(
            f"memory plus swap {host.get('MemorySwap')}, not {LIMIT}: swap is on"
        )
    if reported != LIMIT:
        failures.append(f"the runtime reports a limit of {reported}, not {LIMIT}")
    return failures


def state_failures(inspected: dict[str, Any], started: str, running: bool) -> list[str]:
    """OOM kill, restart and early exit from `docker inspect`; `running`
    while the work is checked, stopped once SIGTERM was sent. Rootless
    docker set `OOMKilled` for one memory cgroup kill of two here, so exit
    code 137 (SIGKILL) is a failure of its own."""
    state = inspected["State"]
    failures = []
    if state.get("OOMKilled"):
        failures.append("the container was OOM-killed")
    if inspected.get("RestartCount") or state.get("StartedAt") != started:
        failures.append("the container restarted")
    if state.get("ExitCode") == 137:
        failures.append("the server was killed (exit code 137), not stopped")
    elif running and (not state.get("Running") or state.get("Restarting")):
        failures.append(f"the container stopped (exit code {state.get('ExitCode')})")
    return failures


def parse_probe(text: str) -> dict[str, int]:
    """The host probe: `peak` in bytes, `memory.events` counters, and `hwm`,
    the largest `VmHWM` in the cgroup, in bytes."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) < 2 or not words[1].isdigit():
            continue
        if words[0] == "hwm":
            found["hwm"] = max(found.get("hwm", 0), int(words[1]) * 1024)
        else:
            found[words[0]] = int(words[1])
    return found


def event_failures(host: dict[str, int]) -> list[str]:
    killed = host.get("oom_kill", 0)
    return [f"the kernel killed {killed} process(es) in the cgroup"] * bool(killed)


def mib(value: int | None) -> str:
    return "not exposed" if not value else f"{value / MIB:.1f} MiB"


def docker(*args: str, check: bool = True, timeout: float = 900) -> str:
    done = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and done.returncode != 0:
        raise ProofFailure(f"docker {args[0]}: {done.stderr.strip()[-800:]}")
    return done.stdout.strip()


def inspect(cid: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = json.loads(docker("inspect", cid))
    return found[0]


def reported_limit(cid: str) -> int | None:
    usage = parse_usage(
        docker("stats", "--no-stream", "--format", "{{.MemUsage}}", cid)
    )
    return usage[1] if usage else None


def logs(cid: str) -> str:
    done = subprocess.run(
        ["docker", "logs", cid],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return done.stdout


def host_probe(cid: str) -> dict[str, int]:
    """The cgroup's figures and the server's VmHWM where the docker host's
    files can be read; empty otherwise."""
    command = [*shlex.split(os.environ.get(HOST_SHELL, "")), "sh", "-c"]
    try:
        done = subprocess.run(
            [*command, PROBE.format(id=cid)], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_probe(done.stdout) if done.returncode == 0 else {}


class Sampler:
    """The container's memory as `docker stats` reports it, once a second."""

    def __init__(self, cid: str) -> None:
        self.cid, self.peak, self.samples = cid, 0, 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = docker(
                    "stats", "--no-stream", "--format", "{{.MemUsage}}", self.cid,
                    check=False, timeout=30,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                text = ""
            usage = parse_usage(text)
            if usage:
                self.peak, self.samples = max(self.peak, usage[0]), self.samples + 1
            self._stop.wait(SAMPLE_SECONDS)

    def __enter__(self) -> Sampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=60)


def call(url: str, route: str, body: dict[str, Any] | None) -> dict[str, Any]:
    """One request with the proof's token; a refusal is a GateFailure."""
    request = urllib.request.Request(
        f"{url}/v1/{route}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}"},
        method="GET" if body is None else "POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=900) as response:
            answer: dict[str, Any] = json.loads(response.read())
            return answer
    except urllib.error.HTTPError as error:
        raise GateFailure(f"{route}: {error.code} {error.read()[:300]!r}") from None


def healthy(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/health", timeout=5) as response:
            return bool(response.status == 200)
    except OSError:
        return False


def read_back(
    url: str, archive: Path, made: list[dict[str, Any]], before: str
) -> dict[str, int]:
    """Units, the archive intact, the listing, every full view with its
    committed digest, and `read_turns` against the archive's bytes."""

    def key(unit: dict[str, Any]) -> tuple[str, str]:
        return str(unit["kind"]), str(unit["name"])

    if sorted(made, key=key) != sorted(units(archive), key=key):
        raise ProofFailure("the import's units are not the archive's")
    if tree_digest(archive) != before:
        raise ProofFailure("the import changed the archive")
    names = conversations(archive)
    listed = call(url, "store/conversations", {})["conversations"]
    if sorted(listed) != names:
        raise ProofFailure(f"the store lists {len(listed)} of {len(names)}")
    read, wanted, pages, sent = Tally(), Tally(), 0, 0
    for name in names:
        walked, digest = full_view(url, archive, name, read, wanted)
        pages += walked
        status = call(
            url, RESTORE, {"conversation_id": name, "restore": restore_id(name)}
        )
        if (archive / "conversations" / name / "turns.jsonl").exists():
            expect(status, state="committed", digest=digest)
        else:
            expect(status, state="none")
        kept = post_raw(url, "conversation/read_turns", {"conversation_id": name})
        whole = [e.turn for e in turns(archive, name, convert=False)]
        differ(
            name,
            "the archive's turns",
            kept,
            compact({"turns": whole, "next_after": None}),
        )
        sent += len(kept)
    if read != wanted or set(read.records) != EVERY_KIND:
        raise ProofFailure("the full views do not hold the archive's records")
    return {
        "conversations": len(names),
        "pages": pages,
        "records": sum(read.records.values()),
        "piece_bytes": sum(read.bytes.values()),
        "read_turns_bytes": sent,
    }


def operations(url: str, store: Store) -> list[dict[str, Any]]:
    """The gate's operations at their largest sizes, each read back."""

    def post(route: str, body: dict[str, Any]) -> dict[str, Any]:
        return call(url, route, body)

    rows = []
    for case in largest_cases():
        conversation = conversation_of(case)
        if case.operation == SWEEP:
            write(post, conversation, STALE, turns_of(case)[0])
            store.age(conversation, STALE)
        started = time.perf_counter()
        items = drive(case, post)
        seconds = time.perf_counter() - started
        if case.operation == SWEEP:
            body = {"conversation_id": conversation, "draft": STALE, "records": []}
            expect(post(WRITES, {**body, "discard": True}), state="discarded")
        row: dict[str, Any] = {"operation": case.operation, "series": case.series}
        row |= {"size": label(case), "items": items, "seconds": round(seconds, 3)}
        rows.append(row)
        print(
            f"  {case.operation:<17}{case.series:<13}{label(case):>13}"
            f"{items:>7} items{seconds:>8.2f} s  ok",
            flush=True,
        )
    return rows


def workload(
    url: str, archive: Path, store: Store, importer: Importer
) -> dict[str, Any]:
    require_records(call(url, "capabilities", None))
    before = tree_digest(archive)
    started = time.monotonic()
    made = importer()
    seconds = round(time.monotonic() - started, 2)
    print(
        f"  import of the one-tenth home: {len(made)} units in {seconds} s", flush=True
    )
    checked = read_back(url, archive, made, before)
    print(f"  read back: {checked}", flush=True)
    return {
        "import_seconds": seconds,
        "read_back": checked,
        "operations": operations(url, store),
    }


def imported(
    command: list[str], env: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    done = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        raise ProofFailure(
            f"the import exited {done.returncode}: {done.stderr[-2000:]}"
        )
    units_made: list[dict[str, Any]] = json.loads(done.stdout)["units"]
    return units_made


def user() -> list[str]:
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


@contextmanager
def legs(name: str, scratch: Path, dsn: str) -> Iterator[tuple[Path, str, Store]]:
    """A leg's store: (a volume to enable, or a schema with records
    extension 1, dropped afterwards), and the Store that ages a draft."""
    data, schema = scratch / "data", ""
    data.mkdir()
    if name == "postgres":
        schema = f"neosian_container_{uuid.uuid4().hex}"
        create(dsn, schema, records=True)
    store = Store(name, Path(ENTRYPOINT), Backend(name, [], {}), dsn=dsn, schema=schema)
    store.home = data if name == "file" else None
    try:
        yield data, schema, store
    finally:
        if schema:
            drop(dsn, schema)


def container_leg(
    image: str, name: str, scratch: Path, dsn: str
) -> tuple[list[str], dict[str, Any]]:
    """One leg in the capped container; its state is read before removal."""
    archive, client = scratch / "archive", scratch / "client"
    write_home(archive, "tenth")
    (client / "home").mkdir(parents=True)
    port = int(os.environ.get("NEOSIAN_CONTAINER_PORT", "16367"))
    evidence: dict[str, Any] = {"leg": name, "image": image, "limit": LIMIT}
    with legs(name, scratch, dsn) as (data, schema, store):
        if name == "file":
            volume = ["-v", f"{data}:/data"]
            docker(
                "run",
                "--rm",
                *user(),
                *volume,
                "--entrypoint",
                ENTRYPOINT,
                image,
                "records",
                "enable",
                "--root",
                "/data",
            )
            options, serve = [*user(), *volume], ["--root", "/data"]
        else:
            target = container_dsn(
                dsn, os.environ.get("NEOSIAN_CONTAINER_POSTGRES_DSN", "")
            )
            options = [
                "--add-host=host.docker.internal:host-gateway",
                "-e",
                f"NEOSIAN_POSTGRES_DSN={target}",
            ]
            serve = ["--schema", schema]
        cid = docker(
            "run", "-d", *LIMIT_OPTIONS, *options, "-e", f"NEOSIAN_SERVE_TOKEN={TOKEN}",
            "-p", f"127.0.0.1:{port}:{INNER_PORT}", image, *serve, "--scope", "user:container",
        )  # fmt: skip
        try:
            return run_capped(
                image, cid, f"http://127.0.0.1:{port}", archive, client, store, evidence
            )
        finally:
            docker("rm", "-f", cid, check=False)


def run_capped(
    image: str,
    cid: str,
    url: str,
    archive: Path,
    client: Path,
    store: Store,
    evidence: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    first = inspect(cid)
    started = first["State"]["StartedAt"]
    reported = reported_limit(cid)
    evidence["reported_limit"] = reported
    failures = limit_failures(first["HostConfig"], reported)
    if failures:
        return failures, evidence  # no work outside the limit
    print(f"  limit: {mib(reported)}, swap disabled ({cid[:12]})", flush=True)
    importer = partial_import(image, cid, archive, client)
    with Sampler(cid) as sampler:
        try:
            wait_healthy(cid, url)
            evidence |= workload(url, archive, store, importer)
        except Exception as error:  # any failure is reported after the state
            failures.append(
                f"the work did not complete: {type(error).__name__}: {error}"
            )
    host = host_probe(cid)
    evidence |= {"sampled_peak": sampler.peak, "samples": sampler.samples, "host": host}
    failures += state_failures(inspect(cid), started, running=True)
    failures += event_failures(host)
    docker("stop", "-t", str(STOP_TIMEOUT), cid, check=False, timeout=STOP_TIMEOUT + 60)
    for _ in range(20):  # the log driver can trail the stop by a beat
        if SHUTDOWN in logs(cid):
            break
        time.sleep(0.5)
    else:
        failures.append("no graceful shutdown in the server's log")
    stopped = inspect(cid)
    evidence["exit_code"] = stopped["State"].get("ExitCode")
    failures += state_failures(stopped, started, running=False)
    print(
        f"  peak: docker stats {mib(sampler.peak)} ({sampler.samples} samples),"
        f" cgroup memory.peak {mib(host.get('peak'))} (page cache included),"
        f" server VmHWM {mib(host.get('hwm'))}; OOMKilled"
        f" {stopped['State'].get('OOMKilled')}, oom_kill {host.get('oom_kill', 'not exposed')},"
        f" restarts {stopped.get('RestartCount')}, exit code {evidence['exit_code']}",
        flush=True,
    )
    return failures, evidence


def partial_import(image: str, cid: str, archive: Path, client: Path) -> Importer:
    """The import from the same image, in the server's network, unlimited."""
    command = [
        "docker", "run", "--rm", "--network", f"container:{cid}", *user(),
        "-v", f"{archive}:/archive", "-v", f"{client}:/client",
        "-e", "HOME=/client", "-e", "NEOSIAN_HOME=/client/home",
        "-e", f"NEOSIAN_CLIENT_TOKEN={TOKEN}", "--entrypoint", ENTRYPOINT, image,
        "import", "/archive", "--url", f"http://127.0.0.1:{INNER_PORT}", "--json",
    ]  # fmt: skip
    return lambda: imported(command)


def wait_healthy(cid: str, url: str) -> None:
    deadline = time.monotonic() + BOOT_TIMEOUT
    while True:
        state = inspect(cid)["State"]
        if not state.get("Running"):
            raise ProofFailure(f"exited before healthy: {logs(cid)[-2000:]}")
        if state.get("Health", {}).get("Status") == "healthy" and healthy(url):
            return
        if time.monotonic() > deadline:
            raise ProofFailure(f"not healthy after {BOOT_TIMEOUT} s")
        time.sleep(1)


def native_leg(
    binary: Path, name: str, scratch: Path, dsn: str
) -> tuple[list[str], dict[str, Any]]:
    """The dry run: the same workload against a native server, no limit."""
    archive, client = scratch / "archive", scratch / "client"
    write_home(archive, "tenth")
    (client / "home").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}
    with legs(name, scratch, dsn) as (data, schema, store):
        if name == "file":
            subprocess.run(
                [str(binary), "records", "enable", "--root", str(data)],
                check=True,
                env=env,
                capture_output=True,
            )
            serve, extra = ["--root", str(data)], {}
        else:
            serve, extra = ["--schema", schema], {"NEOSIAN_POSTGRES_DSN": dsn}
        port = _port()
        process = subprocess.Popen(
            [str(binary), "serve", "--port", str(port), *serve],
            env=env | extra | {"NEOSIAN_SERVE_TOKEN": TOKEN},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        url = f"http://127.0.0.1:{port}"
        client_env = env | {
            "NEOSIAN_HOME": str(client / "home"),
            "NEOSIAN_CLIENT_TOKEN": TOKEN,
        }
        command = [str(binary), "import", str(archive), "--url", url, "--json"]
        failures: list[str] = []
        evidence: dict[str, Any] = {"leg": name, "binary": str(binary), "dry_run": True}
        try:
            for _ in range(BOOT_TIMEOUT * 4):
                if healthy(url):
                    break
                time.sleep(0.25)
            evidence |= workload(
                url, archive, store, lambda: imported(command, client_env)
            )
        except Exception as error:  # reported like the container leg's
            failures.append(
                f"the work did not complete: {type(error).__name__}: {error}"
            )
        finally:
            evidence["server_peak_mib"] = round(_peak(process), 1)
        print(f"  dry run peak (native, no limit): {evidence['server_peak_mib']} MiB")
        return failures, evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--image",
        default=os.environ.get("NEOSIAN_CONTAINER_IMAGE", "neosian:conformance"),
    )
    parser.add_argument(
        "--native", type=Path, help="dry run against this binary, no container"
    )
    parser.add_argument("--evidence", type=Path, help="write what was measured as JSON")
    args = parser.parse_args(argv)
    dsn = os.environ.get(DSN_ENV, "")
    if args.native is None:
        served = docker(
            "image",
            "inspect",
            "-f",
            '{{index .Config.Labels "ai.mausa.neosian.backends"}}',
            args.image,
        )
    else:
        served = "file,postgres"
        print(
            "DRY RUN: a native server without any memory limit; this is not the container proof"
        )
    planned, skipped = plan(served, dsn)
    for note in skipped:
        print(f"== records proof, {note}: SKIPPED")
    failures: list[str] = []
    completed: list[str] = []
    evidence: dict[str, Any] = {}
    tmp = os.environ.get("NEOSIAN_CONTAINER_TMPDIR") or None
    for name in planned:
        print(
            f"== records proof, {name}{'' if args.native else ' in 192 MiB'}",
            flush=True,
        )
        scratch = Path(tempfile.mkdtemp(prefix="neosian-records-", dir=tmp))
        try:
            if args.native is None:
                found, evidence[name] = container_leg(args.image, name, scratch, dsn)
            else:
                found, evidence[name] = native_leg(
                    args.native.resolve(), name, scratch, dsn
                )
        except (ProofFailure, GateFailure, subprocess.CalledProcessError) as error:
            found = [f"{type(error).__name__}: {error}"]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        failures += [f"{name}: {failure}" for failure in found]
        completed.append(name)
    failures += unfinished(planned, completed)
    if args.evidence:
        args.evidence.write_text(json.dumps(evidence, indent=1))
    for failure in failures:
        print(f"FAILED {failure}")
    if not failures:
        print(
            f"== records proof: {', '.join(completed)} {'dry run ' * bool(args.native)}green"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
