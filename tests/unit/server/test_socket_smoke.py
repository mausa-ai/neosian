"""The state process over a real socket — the walkthrough's idiom (§14.5)
applied to `neosian serve`: the literal console script, a real TCP bind,
`RemoteStore` over the network, bearer enforced, and SIGTERM draining to
a clean exit. Keyless; the one test that leaves the process boundary.

The in-process suites cover semantics; this one covers the boundary the
ASGI transport skips — argv, the environment, the socket, the signal.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from neosian import RemoteStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.settings import SERVE_TOKEN_ENV

_TOKEN = "smoke-token"
_BOOT_TIMEOUT = 30.0


def _binary() -> Path:
    name = "neosian.exe" if sys.platform == "win32" else "neosian"
    candidate = Path(sys.executable).parent / name
    # No skip: a missing console script must go red (the walkthrough rule).
    assert candidate.is_file(), f"console script missing: {candidate} (run `uv sync`)"
    return candidate


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


class Served:
    def __init__(self, url: str, root: Path) -> None:
        self.url = url
        self.root = root


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    port = _free_port()
    root = tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if k != "NEOSIAN_POSTGRES_DSN"}
    env[SERVE_TOKEN_ENV] = _TOKEN
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(  # noqa: S603 - the repo's own console script
        [
            str(_binary()),
            "serve",
            "--root",
            str(root),
            "--scope",
            "user:smoke",
            "--actor",
            "serve:smoke",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + _BOOT_TIMEOUT
        while True:
            if process.poll() is not None:
                out, err = process.communicate()
                raise AssertionError(f"serve exited early: {err or out}")
            try:
                if httpx.get(f"{url}/health", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise AssertionError("serve never became healthy")
            time.sleep(0.1)
        yield Served(url, root)
    finally:
        process.terminate()  # SIGTERM: uvicorn's graceful path
        try:
            _, err = process.communicate(timeout=_BOOT_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - a hung server
            process.kill()
            process.wait(timeout=10)
            raise AssertionError("serve did not shut down on SIGTERM") from None
        # Graceful is the drain, not the exit code: uvicorn re-raises the
        # captured signal after shutting down, so the conventional
        # -SIGTERM status is what a clean stop looks like from out here.
        assert "Shutting down" in err
        assert "Application shutdown complete" in err


class TestOverTheWire:
    async def test_health_is_unauthenticated(self, served: Served) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{served.url}/health", timeout=5.0)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_the_bearer_gate_holds_on_a_real_socket(self, served: Served) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{served.url}/v1/memory/read",
                json={"scope": "user:smoke", "path": "x"},
                timeout=5.0,
            )
        assert response.status_code == 401

    async def test_remote_store_writes_and_reads_across_the_network(
        self, served: Served
    ) -> None:
        store = await RemoteStore.connect(served.url, token=_TOKEN)
        try:
            assert type(store).supports_optimistic_concurrency is False
            written = await store.write(
                "user:smoke", "notes/hello", "over the wire", actor="serve:smoke"
            )
            assert written.version == 1
            read_back = await store.read("user:smoke", "notes/hello")
            assert read_back is not None
            assert read_back.content == "over the wire"
            rows = await store.versions("user:smoke", "notes/hello")
            # The daemon prefixes its asserted client (§20): a bare token
            # is `client:default`, and the body actor lands under it.
            assert [(row.version, row.action, row.actor) for row in rows] == [
                (1, "created", f"{store.client}/serve:smoke")
            ]
        finally:
            await store.aclose()
        # Store truth, read from this side of the boundary.
        document = await FileStore(served.root).read("user:smoke", "notes/hello")
        assert document is not None
        assert document.content == "over the wire"

    async def test_conversations_cross_the_same_socket(self, served: Served) -> None:
        from neosian._foundation.llm.base import Message, Role

        store = await RemoteStore.connect(served.url, token=_TOKEN)
        try:
            turn = await store.append_turn(
                "smoke-thread", [Message(role=Role.USER, content="hi")]
            )
            assert turn.turn == 1
            assert await store.last_turn_number("smoke-thread") == 1
            turns = await store.read_turns("smoke-thread")
            assert [message.content for message in turns[0].messages] == ["hi"]
        finally:
            await store.aclose()
