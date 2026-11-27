"""Fixtures for the queue's contracts: a port nothing listens on yet, a
`neosian serve` started on it later, a conversation's landing lock held by
the test, as another process would hold it, and both of a home's landing
slots held, as two running landers hold them, so a hook run meanwhile
starts no lander (ruling D11 of the T1e1 plan)."""

from __future__ import annotations

import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path

import pytest

from tests.contract.support import ADMIN, Binary, Server

Serve = Callable[[int], AbstractContextManager[Server]]
Hold = Callable[[Path], AbstractContextManager[None]]


@pytest.fixture
def port() -> int:
    """A loopback port bound once and released: closed until a test serves it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def serve_on(binary: Binary) -> Serve:
    """`neosian serve --port PORT` over its own root, accepting `ADMIN`."""

    @contextmanager
    def start(port: int) -> Iterator[Server]:
        env = {
            **binary.environment(),
            "NEOSIAN_SERVE_TOKEN": f"client:contract={ADMIN}",
        }
        root = binary.directory / "served"
        process = subprocess.Popen(
            [str(binary.executable), "serve", "--port", str(port), "--root", str(root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            cwd=binary.directory,
        )
        server = Server(f"http://127.0.0.1:{port}")
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, process.communicate()[1]
                try:
                    reply = server.request("/health", method="GET", authenticated=False)
                    if reply.status == 200:
                        break
                except OSError:
                    pass
                assert time.monotonic() < deadline, "serve did not become healthy"
                time.sleep(0.02)
            yield server
        finally:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)

    return start


@pytest.fixture
def hold_lock() -> Hold:
    """Holds `pending/<id>/.lock` under `directory` with `flock`, which is
    what the binary's `try_lock` takes on Linux and macOS."""

    @contextmanager
    def held(directory: Path) -> Iterator[None]:
        import fcntl

        directory.mkdir(parents=True, exist_ok=True)
        lock = directory / ".lock"
        if not lock.exists():
            lock.write_bytes(b"\0")
        with lock.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    return held


SLOTS = ("slot-1.lock", "slot-2.lock")


def slot_files(home: Path) -> list[Path]:
    """The home's two landing slots, `spool/landing/slot-<n>.lock`."""
    return [home / "spool" / "landing" / name for name in SLOTS]


@contextmanager
def slots_held(home: Path) -> Iterator[None]:
    """Both of `home`'s landing slots held with `flock`: a hook run inside
    starts no lander, and a `record-land` run inside exits at once."""
    import fcntl

    with ExitStack() as stack:
        for slot in slot_files(home):
            slot.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(slot.open("a+b"))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
