"""Where answers are spooled, and what happens when they cannot be (§40.46, #419).

Answers past 1 MiB spill to a private file in the temporary directory. When it
cannot be written (unwritable, full, gone), the server finishes the answer in
memory as 2.16.0 held it, within `NEOSIAN_MAX_RESPONSE_BYTES`, and refuses past
that with `value_error`, which the binary answers by asking again in size
pages; it is never a bare 500 or a cut body. A server that cannot spool says so
once at startup. Each server's spools live under its own locked root, and a
server removes only the roots of servers that have ended. A listing asked for
no rows reads none. Expected texts are literal.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.contract.support import ADMIN, Binary, Server, serve
from tests.contract.test_size_paging import (
    LONG,
    SlowReader,
    exchange,
    plant_long,
    raw,
    remote_and_local,
)

READ: tuple[str, dict[str, Any]] = (
    "conversation/read_turns",
    {"conversation_id": LONG},
)
NEWEST: tuple[str, dict[str, Any]] = (
    "conversation/search_turns",
    {"query": "dashboard", "order": "newest", "limit": 500},
)
WARNING = "neosian serve: WARNING: cannot spool answers in the temporary directory "
TEMP_VARIABLE = "TMP" if os.name == "nt" else "TMPDIR"
POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX resource limits")


def answers(binary: Binary, **options: Any) -> list[tuple[int, bytes]]:
    with serve(binary, **options) as server:
        return [raw(server, route, body) for route, body in (READ, NEWEST)]


def unwritable(binary: Binary) -> dict[str, str]:
    # A file where a directory should be: nothing can be made under it, even
    # by a superuser.
    blocked = binary.directory / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    return {TEMP_VARIABLE: str(blocked)}


def test_an_unwritable_temporary_directory_answers_from_memory(
    binary: Binary,
) -> None:
    plant_long(binary.home, 120, 13_000)
    expected = answers(binary)
    assert [status for status, _ in expected] == [200, 200]
    assert all(len(answer) > 3 << 20 for _, answer in expected)
    log: list[str] = []
    blocked = unwritable(binary)
    with serve(binary, extra_env=blocked, log=log) as server:
        # 2.16.0's requests: the same bytes, from memory, with their length.
        for (route, body), (_, whole) in zip((READ, NEWEST), expected, strict=True):
            status, headers, answer = exchange(server, route, body)
            assert (status, answer) == (200, whole)
            assert headers["content-length"] == str(len(whole))
        remote_and_local(binary, server, "continue", LONG)
        remote_and_local(binary, server, "search", "dashboard", "word", "--json")
    assert log[0].count(WARNING) == 1, log[0]
    assert f"({TEMP_VARIABLE})" in log[0] and "NEOSIAN_MAX_RESPONSE_BYTES" in log[0]
    # Past the response budget the answer is refused as an operation budget,
    # and the binary's size pages, each under 1 MiB, answer it.
    budget = {**blocked, "NEOSIAN_MAX_RESPONSE_BYTES": str(2 << 20)}
    with serve(binary, extra_env=budget) as server:
        status, answer = raw(server, *READ)
        assert status == 400
        error = json.loads(answer)["error"]
        assert error["code"] == "value_error"
        assert error["message"].startswith(
            "resource limit NEOSIAN_MAX_RESPONSE_BYTES exceeded: "
        )
        assert error["message"].endswith(
            f" > {2 << 20}; select a smaller batch or raise this setting"
        )
        remote_and_local(binary, server, "continue", LONG)


def no_larger_files(limit: int) -> Any:
    def apply() -> None:
        import resource
        import signal

        # Writes past `limit` fail with EFBIG, as on a full disk; the signal
        # that would end the process instead is ignored, across exec.
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    return apply


@POSIX
def test_a_spool_that_fails_midway_is_still_the_whole_answer(binary: Binary) -> None:
    plant_long(binary.home, 120, 13_000)
    expected = answers(binary)
    failing = answers(binary, preexec=no_larger_files(3 << 20))
    assert failing == expected


def root(temporary: Path) -> Path:
    """A server's spool root as one that ended leaves it: an answer still in
    it, its lock file no longer held."""
    directory = temporary / f".neosian-serve-{uuid.uuid4()}"
    answer = directory / f".neosian-stage-{uuid.uuid4()}"
    answer.mkdir(parents=True, mode=0o700)
    (answer / "answer.json").write_text("{" * 1000, encoding="utf-8")
    (directory / "lock").write_bytes(b"\0")
    return directory


def roots(temporary: Path) -> set[str]:
    return {path.name for path in temporary.iterdir()}


def start(
    binary: Binary, temporary: Path, started: list[subprocess.Popen[bytes]]
) -> tuple[subprocess.Popen[bytes], Server]:
    """A server of this test's own, recorded in `started` so the test stops
    it whatever happens."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **binary.environment(),
        "NEOSIAN_SERVE_TOKEN": f"client:contract={ADMIN}",
        "TMPDIR": str(temporary),
    }
    process = subprocess.Popen(
        [
            str(binary.executable),
            "serve",
            "--port",
            str(port),
            "--root",
            str(binary.home),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    started.append(process)
    server = Server(f"http://127.0.0.1:{port}")
    deadline = time.monotonic() + 20
    while True:
        try:
            if (
                server.request("/health", method="GET", authenticated=False).status
                == 200
            ):
                return process, server
        except OSError:
            pass
        assert process.poll() is None and time.monotonic() < deadline
        time.sleep(0.02)


@POSIX
def test_a_server_removes_only_the_spools_of_servers_that_ended(
    binary: Binary,
) -> None:
    plant_long(binary.home, 120, 13_000)
    temporary = binary.directory / "tmp"
    temporary.mkdir(mode=0o700)
    lookalikes = {
        f".neosian-stage-{uuid.uuid4()}",
        ".neosian-serve-not-a-uuid",
        f"neosian-serve-{uuid.uuid4()}",
    }
    for name in lookalikes:
        (temporary / name).mkdir()
    started: list[subprocess.Popen[bytes]] = []
    try:
        # A server killed while it sent an answer leaves its spool behind.
        killed, server = start(binary, temporary, started)
        reader = SlowReader(server, *READ)
        reader.take(1 << 16)
        stale = roots(temporary) - lookalikes
        assert len(stale) == 1
        [left] = stale
        assert any((temporary / left).glob(".neosian-stage-*/answer.json"))
        killed.kill()
        killed.communicate(timeout=10)
        reader.close()
        ended = root(temporary).name
        # A live server's root is kept by every other server that starts.
        live, _ = start(binary, temporary, started)
        ours = roots(temporary) - lookalikes - {left, ended}
        with serve(binary, extra_env={"TMPDIR": str(temporary)}) as server:
            remaining = roots(temporary)
            assert left not in remaining and ended not in remaining
            assert ours <= remaining and lookalikes <= remaining
            assert raw(server, *READ)[0] == 200
        live.terminate()
        _, stderr = live.communicate(timeout=10)
        assert (
            "removed 2 spool directories left by servers that ended" in stderr.decode()
        )
        # Stopped servers take their own roots with them.
        assert roots(temporary) == lookalikes
    finally:
        for process in started:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_a_listing_asked_for_no_rows_reads_none(binary: Binary) -> None:
    # A journal row that cannot be read is found by any read of the scope's
    # history; `limit: 0` reads nothing, so it answers.
    with serve(binary) as server:
        for content in ("one", "two"):
            server.post(
                "memory/write", scope="user:contract", path="a.md", content=content
            )
        [journal] = binary.home.rglob("versions/a.md.jsonl")
        with journal.open("a", encoding="utf-8") as handle:
            handle.write("not json\n")
        for route, key in (
            ("memory/history", {}),
            ("memory/versions", {"path": "a.md"}),
        ):
            body = {"scope": "user:contract", **key}
            assert server.post(route, **body, limit=0) == {
                "versions": [],
                "next_cursor": None,
            }
            assert server.request("/v1/" + route, {**body, "limit": 1}).status != 200
