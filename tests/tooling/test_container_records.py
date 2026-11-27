"""The records proof in 192 MiB checks what it declares and fails what it should.

Expected values are written here from DESIGN §40.54 item 16.4 and the T1gc
outline of the T1f plan, not read from the script. The docker calls are
replaced by a fake that answers as `docker inspect` and `docker stats` do,
so the proof's decisions are checked without a daemon.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import container_records as proof
from scripts.bench_records_memory import label, matrix

ROOT = Path(__file__).resolve().parents[2]
MIB = 1 << 20
LIMIT = 192 * MIB
LARGEST = [
    ("write part", "result", "16 MiB"),
    ("seal", "result", "16 MiB"),
    ("write part", "messages", "16 MiB"),
    ("seal", "messages", "16 MiB"),
    ("write tiny part", "tiny", "2048 records"),
    ("piece fetch", "result", "16 MiB"),
    ("restore part", "result", "16 MiB"),
    ("commit", "result", "16 MiB"),
    ("restore part", "messages", "16 MiB"),
    ("commit", "messages", "16 MiB"),
    ("restore part", "conversation", "64 MiB"),
    ("commit", "conversation", "64 MiB"),
    ("read full", "conversation", "64 MiB"),
    ("read light", "conversation", "64 MiB"),
    ("search newest", "conversation", "64 MiB"),
    ("search relevance", "conversation", "64 MiB"),
    ("sweep preview", "result", "16 MiB"),
]
CAPPED = {"Memory": LIMIT, "MemorySwap": LIMIT}
STARTED = "2026-11-28T09:00:00.000000001Z"


def state(**changes: Any) -> dict[str, Any]:
    """`docker inspect` of a healthy running container, with `changes`."""
    base: dict[str, Any] = {
        "Running": True,
        "Restarting": False,
        "OOMKilled": False,
        "ExitCode": 0,
        "StartedAt": STARTED,
        "Health": {"Status": "healthy"},
    }
    restarts = changes.pop("RestartCount", 0)
    return {"State": base | changes, "RestartCount": restarts}


def test_the_operations_are_the_gate_s_at_their_largest_sizes() -> None:
    cases = [(c.operation, c.series, label(c)) for c in proof.largest_cases()]
    assert cases == LARGEST
    every = {(c.operation, c.series) for c in matrix()}
    assert {(op, series) for op, series, _ in LARGEST} == every


def test_the_limit_is_192_mib_with_swap_disabled() -> None:
    assert proof.LIMIT_OPTIONS == ("--memory", "192m", "--memory-swap", "192m")
    assert proof.LIMIT == LIMIT
    assert proof.parse_size("192MiB") == LIMIT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45.3MiB / 192MiB", (round(45.3 * MIB), LIMIT)),
        ("812KiB / 192MiB", (812 * 1024, LIMIT)),
        ("1.2GiB / 3.805GiB", (round(1.2 * 1024 * MIB), round(3.805 * 1024 * MIB))),
        ("0B / 0B", (0, 0)),
        ("--", None),
        ("", None),
        ("12MiB / lots", None),
    ],
)
def test_docker_stats_usage_is_read(
    text: str, expected: tuple[int, int] | None
) -> None:
    assert proof.parse_usage(text) == expected


def test_a_capped_container_passes_the_limit_check() -> None:
    assert proof.limit_failures(CAPPED, LIMIT) == []


def test_a_container_without_the_limit_is_refused_before_any_work() -> None:
    failures = proof.limit_failures({"Memory": 0, "MemorySwap": 0}, 3_895 * MIB)
    assert failures == [
        f"configured memory limit 0, not {LIMIT}",
        f"memory plus swap 0, not {LIMIT}: swap is on",
        f"the runtime reports a limit of {3_895 * MIB}, not {LIMIT}",
    ]


def test_swap_beside_the_limit_or_a_discarded_limit_fails() -> None:
    swap = proof.limit_failures({"Memory": LIMIT, "MemorySwap": -1}, LIMIT)
    assert swap == [f"memory plus swap -1, not {LIMIT}: swap is on"]
    discarded = proof.limit_failures(CAPPED, None)
    assert discarded == [f"the runtime reports a limit of None, not {LIMIT}"]


def test_a_running_container_that_did_its_work_passes() -> None:
    assert proof.state_failures(state(), STARTED, running=True) == []
    stopped = state(Running=False, ExitCode=0)
    assert proof.state_failures(stopped, STARTED, running=False) == []


def test_an_oom_kill_fails_by_name_even_while_running() -> None:
    killed = state(Running=False, OOMKilled=True, ExitCode=137)
    failures = proof.state_failures(killed, STARTED, running=True)
    assert "the container was OOM-killed" in failures
    still = proof.state_failures(state(OOMKilled=True), STARTED, running=True)
    assert still == ["the container was OOM-killed"]
    after = proof.state_failures(killed, STARTED, running=False)
    assert after[0] == "the container was OOM-killed"


def test_a_kill_without_the_oom_flag_fails_by_its_exit_code() -> None:
    # Rootless docker in the Lima VM: the kernel's memory cgroup killed the
    # server twice, and inspect said OOMKilled false, exit code 137 once.
    rootless = state(Running=False, OOMKilled=False, ExitCode=137)
    killed = ["the server was killed (exit code 137), not stopped"]
    assert proof.state_failures(rootless, STARTED, running=True) == killed
    assert proof.state_failures(rootless, STARTED, running=False) == killed


def test_a_restart_or_an_early_exit_fails() -> None:
    restarted = proof.state_failures(state(RestartCount=1), STARTED, running=True)
    assert restarted == ["the container restarted"]
    again = state(StartedAt="2026-11-28T09:01:00Z")
    assert proof.state_failures(again, STARTED, running=True) == [
        "the container restarted"
    ]
    exited = state(Running=False, ExitCode=101)
    assert proof.state_failures(exited, STARTED, running=True) == [
        "the container stopped (exit code 101)"
    ]
    killed = state(Running=False, ExitCode=137)
    assert proof.state_failures(killed, STARTED, running=False) == [
        "the server was killed (exit code 137), not stopped"
    ]


def test_the_host_probe_reads_the_cgroup_and_the_server() -> None:
    text = (
        "peak 201326592\nlow 0\nhigh 0\nmax 12\noom 3\noom_kill 1\n"
        "oom_group_kill 0\nhwm\t   90112 kB\nhwm\t  1024 kB\n"
    )
    found = proof.parse_probe(text)
    assert found["peak"] == LIMIT
    assert (found["oom"], found["oom_kill"], found["hwm"]) == (3, 1, 88 * MIB)
    assert proof.event_failures(found) == [
        "the kernel killed 1 process(es) in the cgroup"
    ]
    assert proof.event_failures({"oom_kill": 0}) == []
    assert proof.parse_probe("peak \n") == {}


def test_postgres_is_planned_whenever_the_image_serves_it_and_a_dsn_is_set() -> None:
    assert proof.plan("file,postgres", "postgresql://x") == (["file", "postgres"], [])
    assert proof.plan("file", "postgresql://x") == (
        ["file"],
        ["postgres (the image serves: file)"],
    )
    assert proof.plan("file,postgres", "") == (
        ["file"],
        ["postgres (NEOSIAN_TEST_POSTGRES_DSN not set)"],
    )
    assert proof.unfinished(["file", "postgres"], ["file"]) == [
        "the postgres leg did not run"
    ]
    assert proof.unfinished(["file", "postgres"], ["file", "postgres"]) == []


def test_the_container_dsn_moves_only_the_loopback_host() -> None:
    local = "postgresql://localhost:pw@127.0.0.1:5433/localhost"
    assert proof.container_dsn(local) == (
        "postgresql://localhost:pw@host.docker.internal:5433/localhost"
    )
    assert proof.container_dsn("postgresql://u@localhost/db") == (
        "postgresql://u@host.docker.internal/db"
    )
    assert proof.container_dsn("postgresql://db.internal:5432/x") == (
        "postgresql://db.internal:5432/x"
    )
    assert proof.container_dsn(local, "postgresql://172.17.0.2/x") == (
        "postgresql://172.17.0.2/x"
    )


class FakeDocker:
    """Answers as docker does for one container started with `run -d`; the
    limit it reports is the one the run asked for."""

    def __init__(self, after_work: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.capped = False
        self.after_work = after_work
        self.worked = False

    def __call__(self, *args: str, check: bool = True, timeout: float = 0) -> str:
        del check, timeout
        self.calls.append(args)
        if args[:2] == ("run", "-d"):
            self.capped = "--memory" in args
            return "c" * 64
        if args[0] == "inspect":
            host = CAPPED if self.capped else {"Memory": 0, "MemorySwap": 0}
            found = state() | {"HostConfig": host}
            if self.worked and self.after_work is not None:
                found = self.after_work | {"HostConfig": host}
            return json.dumps([found])
        if args[0] == "stats":
            return "9.5MiB / 192MiB" if self.capped else "9.5MiB / 3.805GiB"
        return ""


def fake_world(
    monkeypatch: pytest.MonkeyPatch, fake: FakeDocker, work: Any = None
) -> list[str]:
    done: list[str] = []

    def workload(*_: Any) -> dict[str, Any]:
        done.append("work")
        fake.worked = True
        if work is not None:
            raise work
        return {"import_seconds": 1.0}

    monkeypatch.setattr(proof, "docker", fake)
    monkeypatch.setattr(proof, "workload", workload)
    monkeypatch.setattr(proof, "wait_healthy", lambda *_: None)
    monkeypatch.setattr(proof, "host_probe", lambda _: {"oom_kill": 0})
    monkeypatch.setattr(proof, "logs", lambda _: proof.SHUTDOWN)
    monkeypatch.setattr(proof, "write_home", lambda *_: None)
    monkeypatch.setattr(proof, "SAMPLE_SECONDS", 0.01)
    return done


def test_the_server_container_is_started_with_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeDocker()
    done = fake_world(monkeypatch, fake)
    failures, evidence = proof.container_leg("img", "file", tmp_path, "")
    assert failures == []
    assert done == ["work"]
    run = next(call for call in fake.calls if call[:2] == ("run", "-d"))
    joined = " ".join(run)
    assert " ".join(proof.LIMIT_OPTIONS) in joined
    assert evidence["reported_limit"] == LIMIT
    assert fake.calls[-1] == ("rm", "-f", "c" * 64)


def test_without_the_limit_no_work_runs_and_the_proof_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeDocker()
    done = fake_world(monkeypatch, fake)
    monkeypatch.setattr(proof, "LIMIT_OPTIONS", ())
    failures, _ = proof.container_leg("img", "file", tmp_path, "")
    assert done == []
    assert failures[0] == f"configured memory limit 0, not {LIMIT}"
    assert fake.calls[-1] == ("rm", "-f", "c" * 64)


def test_an_oom_kill_during_the_work_is_read_before_the_container_goes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    killed = state(Running=False, OOMKilled=True, ExitCode=137)
    fake = FakeDocker(after_work=killed)
    fake_world(monkeypatch, fake, ConnectionResetError("reset by peer"))
    failures, evidence = proof.container_leg("img", "file", tmp_path, "")
    assert failures[0].startswith("the work did not complete: ConnectionResetError")
    assert "the container was OOM-killed" in failures
    assert "the server was killed (exit code 137), not stopped" in failures
    assert evidence["exit_code"] == 137
    inspected = [i for i, call in enumerate(fake.calls) if call[0] == "inspect"]
    assert (
        inspected[-1] < len(fake.calls) - 1 == fake.calls.index(("rm", "-f", "c" * 64))
    )


def test_make_and_ci_run_the_proof_with_both_legs() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "\t./scripts/container_test.sh $(part)\n" in makefile
    script = (ROOT / "scripts/container_test.sh").read_text()
    assert 'uv run python scripts/container_records.py --image "$IMAGE"' in script
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    job = ci[ci.index("\n  container:\n") :]
    assert "      - run: make test-container\n" in job
    assert "NEOSIAN_TEST_POSTGRES_DSN: postgresql://" in job
    assert "timeout-minutes: 30" in job[: job.index("services:")]


def test_both_legs_run_when_the_image_serves_postgres_and_a_dsn_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[str] = []

    def leg(image: str, name: str, *_: Any) -> tuple[list[str], dict[str, Any]]:
        ran.append(f"{image} {name}")
        return [], {}

    monkeypatch.setenv("NEOSIAN_TEST_POSTGRES_DSN", "postgresql://x")
    monkeypatch.setenv("NEOSIAN_CONTAINER_TMPDIR", str(tmp_path))
    monkeypatch.setattr(proof, "docker", lambda *_, **__: "file,postgres")
    monkeypatch.setattr(proof, "container_leg", leg)
    assert proof.main(["--image", "img"]) == 0
    assert ran == ["img file", "img postgres"]
