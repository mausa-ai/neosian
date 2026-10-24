"""Interrupted and competing real receiver processes, without fault switches."""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.contract.exchange_support import SCOPE, apply, export, files, receiver
from tests.contract.support import Binary, serve


@pytest.mark.skipif(os.name != "posix", reason="process-kill tier uses POSIX")
@pytest.mark.parametrize("operation", ["snapshot", "delta", "erase"])
def test_interrupted_receiver_recovers_data_history_and_checkpoint(
    binary: Binary, operation: str
) -> None:
    for n in range(20):
        result = binary.run(
            "memory",
            "create",
            f"/memories/doc-{n}",
            "--scope",
            SCOPE,
            "--content",
            "-",
            stdin="private-exchange-marker " * 10000,
        )
        assert result.returncode == 0, result.stderr
    snap = export(binary, "snapshot")
    bundle = snap
    if operation != "snapshot":
        apply(binary, snap)
        if operation == "delta":
            binary.json(
                "memory",
                "rename",
                "/memories/doc-0",
                "/memories/moved",
                "--scope",
                SCOPE,
            )
        else:
            binary.json("memory", "redact", "/memories", "--all", "--scope", SCOPE)
        bundle = export(binary, "delta", after=snap)
    pending = binary.directory / "replica/.transactions/pending.json"
    process = subprocess.Popen(
        [
            str(binary.executable),
            "exchange",
            "apply",
            str(bundle),
            *receiver(binary),
            "--write",
        ],
        cwd=binary.directory,
        env=binary.environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not pending.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.0001)
        assert (
            pending.exists()
        ), "receiver finished without observing its committed journal"
        process.kill()
        process.wait(timeout=10)
        assert pending.is_file()
        status = binary.json("exchange", "status", *receiver(binary))
        expected = json.loads(bundle.read_text())
        assert (
            status["status"] == "ready"
            and status["checkpoint"] == expected["checkpoint"]
        )
        assert not pending.exists()
        before = files(binary)
        assert (
            apply(binary, bundle)["status"] == "duplicate" and files(binary) == before
        )
        assert not list((binary.directory / "replica").rglob(".neosian-tmp-*"))
        if operation == "erase":
            assert b"private-exchange-marker" not in b"".join(files(binary).values())
        else:
            assert b"private-exchange-marker" in b"".join(files(binary).values())
        if operation == "delta":
            assert not (
                binary.directory / "replica/user%3Aexchange/documents/doc-0.md"
            ).exists()
            assert (
                binary.directory / "replica/user%3Aexchange/documents/moved.md"
            ).exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_competing_receivers_commit_once_and_deltas_span_pages(binary: Binary) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope=SCOPE, path="a", content="start")
        snap = export(binary, "snapshot")
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(lambda _: apply(binary, snap), range(6)))
        assert [r["status"] for r in results].count("applied") == 1
        assert [r["status"] for r in results].count("duplicate") == 5
        for n in range(105):
            server.post("memory/write", scope=SCOPE, path="a", content=str(n))
        delta = export(binary, "delta", after=snap)
        assert len(json.loads(delta.read_text())["updates"]) == 1
        apply(binary, delta)
        rows = binary.json("memory", "versions", "/memories/a", *receiver(binary))[
            "versions"
        ]
        assert [r["version"] for r in rows] == [106, 1]
        assert [r["content"] for r in rows] == ["104", "start"]
