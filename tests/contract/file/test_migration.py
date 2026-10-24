"""The explicit v1 YAML migration preserves data and refuses ambiguous input."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contract.file.support import DOCUMENT, SCOPE, snapshot
from tests.contract.storage.support import refused
from tests.contract.support import Binary, serve

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/migrate_v1_metadata.py"


def migrate(binary: Binary, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(binary.home), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def add_metadata(binary: Binary, text: str) -> bytes:
    path = binary.home / DOCUMENT
    before = path.read_bytes()
    head, body = before[4:].split(b"---\n", 1)
    changed = b"---\n" + head + text.encode() + b"---\n" + body
    path.write_bytes(changed)
    return changed


def test_block_metadata_refuses_wire_mutations_until_explicit_migration(
    planted: Binary,
) -> None:
    original = add_metadata(
        planted, "nested:\n  flags: [true, null, 7]\n  title: 'café'\n"
    )
    before = snapshot(planted.home)
    with serve(planted) as server:
        for route, fields in [
            ("read", {}),
            ("write", {"content": "must not be written"}),
            ("rename", {"dst": "moved"}),
        ]:
            body = {"scope": SCOPE, "path": "notes/fact", **fields}
            if route == "rename":
                body["src"] = body.pop("path")
            refused(
                server.request("/v1/memory/" + route, body),
                "memory_format_unsupported",
            )
            assert snapshot(planted.home) == before
    preview = migrate(planted)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["applied"] is False
    assert snapshot(planted.home) == before
    applied = migrate(planted, "--write")
    assert applied.returncode == 0, applied.stderr
    path = planted.home / DOCUMENT
    assert path.with_suffix(".md.v1-backup").read_bytes() == original
    assert path.read_bytes().split(b"---\n", 2)[2] == original.split(b"---\n", 2)[2]
    for name, data in before.items():
        if name.endswith(".jsonl"):
            assert (planted.home / name).read_bytes() == data
    assert json.loads(migrate(planted, "--write").stdout)["documents"] == []
    with serve(planted) as server:
        document = server.post("memory/read", scope=SCOPE, path="notes/fact")[
            "document"
        ]
        assert document["extra"]["nested"] == {
            "flags": [True, None, 7],
            "title": "café",
        }
        rewritten = server.post(
            "memory/write", scope=SCOPE, path="notes/fact", content="updated"
        )["document"]
        assert rewritten["extra"] == document["extra"]


@pytest.mark.parametrize(
    "metadata",
    [
        "nonjson: !!set {x: null}\n",
        "nested: {1: value}\n",
        "date: 2020-01-01\n",
        "float: .nan\n",
        "loop: &loop [*loop]\n",
    ],
)
def test_non_json_metadata_is_refused_before_any_file_or_backup(
    planted: Binary, metadata: str
) -> None:
    add_metadata(planted, metadata)
    before = snapshot(planted.home)
    result = migrate(planted, "--write")
    assert result.returncode == 1
    assert "migration refused" in result.stderr
    assert snapshot(planted.home) == before


def test_existing_backup_stops_the_entire_plan(planted: Binary) -> None:
    (planted.home / DOCUMENT).with_suffix(".md.v1-backup").write_bytes(b"keep me")
    before = snapshot(planted.home)
    result = migrate(planted, "--write")
    assert result.returncode == 1
    assert "backup already exists" in result.stderr
    assert snapshot(planted.home) == before
