"""Independent physical checks: kill a real writer, then recover through a new CLI."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from typing import Any

import pytest

from tests.contract.support import Binary


def poll(binary: Binary, *args: str) -> dict[str, Any]:
    result = binary.run("changes", "read", "--scope", "user:recovery", *args)
    assert result.returncode == 0, result.stderr
    page: dict[str, Any] = json.loads(result.stdout)
    return page


def seed(binary: Binary) -> None:
    result = binary.run(
        "memory",
        "create",
        "/memories/doc",
        "--scope",
        "user:recovery",
        "--content",
        "-",
        stdin="recovery-private-marker " * 25000,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "posix", reason="process-kill recovery tier uses POSIX")
@pytest.mark.parametrize("operation", ["create", "rename", "redact", "prune"])
def test_killed_writer_recovers_state_and_matching_checkpoint(
    binary: Binary, operation: str
) -> None:
    seed(binary)
    if operation == "prune":
        # Enough real retained events to observe the journal on fast CI disks.
        for n in range(40):
            binary.json(
                "memory",
                "create",
                f"/memories/small-{n}",
                "--scope",
                "user:recovery",
                "--content",
                "small",
            )
    before = poll(binary)
    pending = binary.home / ".transactions/pending.json"
    args = {
        "create": ["create", "/memories/doc", "--content", "-"],
        "rename": ["rename", "/memories/doc", "/memories/moved"],
        "redact": ["redact", "/memories/doc"],
        "prune": ["prune", "--through", before["cursor"], "--write"],
    }[operation]
    process = subprocess.Popen(
        [
            str(binary.executable),
            "changes" if operation == "prune" else "memory",
            *args,
            "--scope",
            "user:recovery",
        ],
        cwd=binary.directory,
        env=binary.environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        if operation == "create":
            process.stdin.write(b"changed-private-marker " * 25000)
        process.stdin.close()
        deadline = time.monotonic() + 10
        while (
            not pending.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.0001)
        assert pending.exists(), "writer finished without observing its redo journal"
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0 and pending.is_file()
        # A separate process recovers before answering. A second restart is identical.
        after = poll(binary, "--cursor", before["cursor"])
        assert not pending.exists()
        assert after == poll(binary, "--cursor", before["cursor"])
        if operation == "prune":
            assert (
                after["events"] == [] and after["earliest_cursor"] == before["cursor"]
            )
            assert poll(binary)["gap"] == "retention"
            assert not list((binary.home / "user%3Arecovery/.changes/events").iterdir())
            doc = binary.home / "user%3Arecovery/documents/doc.md"
            assert "recovery-private-marker" in doc.read_text()
            seed(binary)
            assert (
                poll(binary, "--cursor", before["cursor"])["events"][0]["sequence"]
                == before["events"][-1]["sequence"] + 1
            )
            return
        assert len(after["events"]) == 1
        event = after["events"][0]
        assert event["sequence"] == 3 and event["stream"] == before["stream"]
        assert (
            event["kind"]
            == {
                "create": "state.document_modified",
                "rename": "state.document_renamed",
                "redact": "state.document_erased",
            }[operation]
        )
        scope = binary.home / "user%3Arecovery"
        rows = [
            json.loads(line)
            for line in (scope / "versions/doc.jsonl").read_text().splitlines()
        ]
        assert [r["version"] for r in rows] == (
            [1] if operation == "redact" else [1, 2]
        )
        if operation == "rename":
            assert not (scope / "documents/doc.md").exists()
            assert (
                "recovery-private-marker" in (scope / "documents/moved.md").read_text()
            )
        elif operation == "create":
            assert "changed-private-marker" in (scope / "documents/doc.md").read_text()
        else:
            assert all(row["redacted"] for row in rows)
            assert all(
                b"recovery-private-marker" not in p.read_bytes()
                for p in binary.home.rglob("*")
                if p.is_file()
            )
        assert not list(binary.home.rglob(".neosian-tmp-*"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


@pytest.mark.parametrize(
    "damage", ["missing", "unknown", "future", "wrong-scope", "wrong-sequence"]
)
def test_feed_corruption_refuses_instead_of_silently_skipping(
    binary: Binary, damage: str
) -> None:
    seed(binary)
    event_file = (
        binary.home / "user%3Arecovery/.changes/events/00000000000000000002.json"
    )
    event = json.loads(event_file.read_text())
    if damage == "missing":
        event_file.unlink()
    else:
        key, value = {
            "unknown": ("unrecognized", True),
            "future": ("format", 2),
            "wrong-scope": ("scope", "user:other"),
            "wrong-sequence": ("sequence", 42),
        }[damage]
        event[key] = value
        event_file.write_text(json.dumps(event))
    result = binary.run("changes", "read", "--scope", "user:recovery")
    assert result.returncode == 1


def test_replaced_stream_and_future_cursor_are_explicit(binary: Binary) -> None:
    seed(binary)
    page = poll(binary)

    def cursor(stream: str, sequence: int) -> str:
        return (
            base64.urlsafe_b64encode(
                json.dumps([1, "user:recovery", stream, sequence]).encode()
            )
            .decode()
            .rstrip("=")
        )

    replaced = poll(
        binary, "--cursor", cursor("11111111-2222-4333-8444-555555555555", 2)
    )
    assert replaced["gap"] == "stream_reset" and replaced["events"] == []
    assert replaced["cursor"] == page["earliest_cursor"]
    result = binary.run(
        "changes",
        "read",
        "--scope",
        "user:recovery",
        "--cursor",
        cursor(page["stream"], 99),
    )
    assert result.returncode == 1


@pytest.mark.parametrize(
    "plan",
    [
        {"format": 2, "writes": []},
        {
            "format": 1,
            "writes": [{"path": "../outside", "operation": "put", "text": "wrong"}],
        },
        {
            "format": 1,
            "writes": [
                {
                    "path": "user%3Arecovery/documents/doc.md",
                    "operation": "put",
                    "text": "wrong",
                    "future": True,
                }
            ],
        },
    ],
)
def test_unsafe_recovery_plan_blocks_before_touching_state(
    binary: Binary, plan: dict[str, Any]
) -> None:
    seed(binary)
    doc = binary.home / "user%3Arecovery/documents/doc.md"
    before = doc.read_bytes()
    pending = binary.home / ".transactions/pending.json"
    pending.write_text(json.dumps(plan))
    result = binary.run("changes", "read", "--scope", "user:recovery")
    assert result.returncode == 1 and pending.exists()
    assert doc.read_bytes() == before
