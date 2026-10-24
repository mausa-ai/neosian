"""The two state processes over one isolated Postgres schema."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from neosian import PostgresStore, RemoteStore
from neosian._foundation.server.app import build_app
from tests.differential.harness import free_port, rust_door, wait_healthy
from tests.support.postgres import store_schema


@asynccontextmanager
async def remote_postgres(
    store: PostgresStore,
    dsn: str,
    directory: Path,
    implementation: str,
    token: str,
) -> AsyncIterator[RemoteStore]:
    """The Rust leg runs a real binary, with SQL planting left to the caller."""
    if implementation == "python":
        app = await build_app(store, token=token)
        client = await RemoteStore.connect(
            "http://state-process", token=token, transport=httpx.ASGITransport(app=app)
        )
        try:
            yield client
        finally:
            await client.aclose()
        return
    binary = rust_door()
    if binary is None:
        pytest.skip("no binary: `cargo build --locked` or set NEOSIAN_BINARY")
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEOSIAN_")}
    env.update(NEOSIAN_POSTGRES_DSN=dsn, NEOSIAN_SERVE_TOKEN=token)
    server = subprocess.Popen(
        [str(binary), "serve", "--schema", store_schema(store), "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=directory,
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(server, url)
        client = await RemoteStore.connect(url, token=token)
        try:
            yield client
        finally:
            await client.aclose()
    finally:
        server.terminate()
        try:
            _, stderr = server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()
            pytest.fail("Rust Postgres server did not stop gracefully")
        assert server.returncode == 0, stderr.decode(errors="replace")
        assert b"Application shutdown complete" in stderr
