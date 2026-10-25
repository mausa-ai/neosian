"""Bulk operations cross old ceilings; explicit budgets fail without truncation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.contract.exchange_support import SCOPE, files, receiver, save
from tests.contract.knowledge_support import invoke, success, unpack
from tests.contract.profile_support import run
from tests.contract.support import Binary, serve


def bulk(binary: Binary, *args: str) -> dict[str, Any]:
    return success(
        subprocess.run(
            [str(binary.executable), *args],
            cwd=binary.directory,
            env={**binary.environment(), "TMPDIR": str(binary.directory)},
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    )


@pytest.mark.timeout(180)
def test_knowledge_import_export_exceeds_old_document_and_byte_caps(
    binary: Binary,
) -> None:
    source = binary.directory / "input"
    source.mkdir()
    count = 1025
    for n in range(count):
        (source / f"doc-{n:04}.md").write_text(
            f"document {n}\n" + "capacity é\n" * 1800, encoding="utf-8"
        )
    imported = bulk(
        binary,
        "knowledge",
        "import",
        str(source),
        "--to",
        "/memories/notes",
        "--scope",
        "user:knowledge",
        "--write",
        "--json",
    )
    assert imported["success"] and len(imported["applied"]) == count
    output = binary.directory / "export"
    result = bulk(
        binary,
        "knowledge",
        "export",
        "/memories/notes",
        "--output",
        str(output),
        "--scope",
        "user:knowledge",
        "--json",
    )
    assert result["documents"] == count
    manifest = json.loads((output / "knowledge.json").read_text())
    assert len(manifest["documents"]) == count
    for record in manifest["documents"]:
        metadata, body = unpack((output / record["file"]).read_text())
        assert body == (source / f'{record["path"]}.md').read_text()
        assert record["origin"]["version"] == 1 and metadata["sources"]
    assert not list(binary.directory.glob(".neosian-stage-*"))


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS", "1"),
        ("NEOSIAN_KNOWLEDGE_MAX_BYTES", "100"),
        ("NEOSIAN_MAX_TEMP_BYTES", "100"),
        ("NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS", "0"),
        ("NEOSIAN_KNOWLEDGE_MAX_BYTES", "not-a-number"),
        ("NEOSIAN_KNOWLEDGE_MAX_BYTES", str(2**31 + 1)),
    ],
)
def test_import_budget_refuses_before_writing_and_can_be_raised(
    binary: Binary, setting: str, value: str
) -> None:
    source = binary.directory / "input"
    source.mkdir()
    for name in ["a", "b"]:
        (source / f"{name}.md").write_text("source " * 100, encoding="utf-8")
    refused = invoke(
        binary,
        "import",
        str(source),
        "--to",
        "/memories/notes",
        "--write",
        env={setting: value},
    )
    assert refused.returncode != 0 and setting in refused.stdout + refused.stderr
    assert not list(binary.home.rglob("*.md"))
    result = success(
        invoke(
            binary,
            "import",
            str(source),
            "--to",
            "/memories/notes",
            "--write",
        )
    )
    assert len(result["applied"]) == 2


@pytest.mark.parametrize(
    "setting", ["NEOSIAN_MAX_TEMP_BYTES", "NEOSIAN_KNOWLEDGE_MAX_BYTES"]
)
def test_export_budget_leaves_no_partial_bundle(binary: Binary, setting: str) -> None:
    source = binary.directory / "a.md"
    source.write_text("preserve " * 100, encoding="utf-8")
    success(invoke(binary, "import", str(source), "--to", "/memories/notes", "--write"))
    before = {str(p): p.read_bytes() for p in binary.home.rglob("*.md")}
    output = binary.directory / "export"
    result = invoke(
        binary,
        "export",
        "/memories/notes",
        "--output",
        str(output),
        env={setting: "100"},
    )
    assert result.returncode != 0 and setting in result.stdout + result.stderr
    assert not output.exists() and not list(binary.directory.glob(".neosian-stage-*"))
    assert before == {str(p): p.read_bytes() for p in binary.home.rglob("*.md")}


def bundle(
    binary: Binary, number: int, base: dict[str, Any] | None, paths: range
) -> Path:
    record = {
        "content": "preserved observation é",
        "version": 1,
        "created_at": "2026-10-20T01:02:03Z",
        "updated_at": "2026-10-21T04:05:06Z",
        "actor": "opaque source actor",
        "redacted": False,
        "extra": {"nested": [1, None, "preserved"]},
    }
    return save(
        binary.directory / f"batch-{number}.json",
        {
            "format": 1,
            "base": base,
            "checkpoint": {
                "format": 1,
                "scope": SCOPE,
                "stream": "11111111-2222-4333-8444-555555555555",
                "cursor": f"opaque-{number}",
            },
            "updates": [
                {"path": f"doc-{n:04}", "document": record, "deletion": None}
                for n in paths
            ],
            "erasures": [],
        },
    )


def test_replica_grows_past_bundle_path_limit_without_losing_receipt(
    binary: Binary,
) -> None:
    first = bundle(binary, 1, None, range(1000))
    applied = bulk(
        binary, "exchange", "apply", str(first), *receiver(binary), "--write"
    )
    second = bundle(binary, 2, applied["checkpoint"], range(1000, 1025))
    before = files(binary)
    refused = run(
        binary,
        "exchange",
        "apply",
        str(second),
        *receiver(binary),
        "--write",
        env={"NEOSIAN_REPLICA_MAX_PATHS": "1000"},
    )
    assert refused.returncode != 0 and "NEOSIAN_REPLICA_MAX_PATHS" in refused.stderr
    assert files(binary) == before
    bulk(binary, "exchange", "apply", str(second), *receiver(binary), "--write")
    assert binary.json("exchange", "status", *receiver(binary))["status"] == "ready"
    assert len(list((binary.directory / "replica").rglob("*.md"))) == 1025
    assert (
        bulk(binary, "exchange", "apply", str(second), *receiver(binary), "--write")[
            "status"
        ]
        == "duplicate"
    )
    document = binary.json("memory", "view", "/memories/doc-0000", *receiver(binary))
    assert "preserved observation é" in json.dumps(document, ensure_ascii=False)


@pytest.mark.parametrize(
    "setting",
    ["NEOSIAN_REPLICA_MAX_BYTES", "NEOSIAN_MAX_TEMP_BYTES", "NEOSIAN_MAX_FILE_BYTES"],
)
def test_receiver_budget_does_not_install_partial_receipt(
    binary: Binary, setting: str
) -> None:
    path = bundle(binary, 1, None, range(2))
    result = run(
        binary,
        "exchange",
        "apply",
        str(path),
        *receiver(binary),
        "--write",
        env={setting: "100"},
    )
    assert result.returncode != 0 and setting in result.stdout + result.stderr
    assert not files(binary)
    assert not list((binary.directory / "replica").rglob(".neosian-tmp-*"))
    bulk(binary, "exchange", "apply", str(path), *receiver(binary), "--write")
    assert binary.json("exchange", "status", *receiver(binary))["status"] == "ready"


def test_committed_recovery_ignores_newly_lowered_preparation_budget(
    binary: Binary,
) -> None:
    binary.json(
        "memory", "create", "/memories/a", "--scope", SCOPE, "--content", "before"
    )
    document = binary.home / "user%3Aexchange/documents/a.md"
    assert document.exists()
    root = document.parents[2]
    pending = root / ".transactions/pending.json"
    pending.parent.mkdir(exist_ok=True)
    text = document.read_text().replace("before", "recovered")
    pending.write_text(
        json.dumps(
            {
                "format": 1,
                "writes": [
                    {
                        "path": str(document.relative_to(root)),
                        "operation": "put",
                        "text": text,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run(
        binary,
        "memory",
        "view",
        "/memories/a",
        "--scope",
        SCOPE,
        env={"NEOSIAN_MAX_TEMP_BYTES": "1"},
    )
    assert result.returncode == 0 and "recovered" in result.stdout
    assert not pending.exists()


@pytest.mark.parametrize(
    "setting", ["NEOSIAN_MAX_FILE_BYTES", "NEOSIAN_MAX_TEMP_BYTES"]
)
def test_file_pages_stop_at_lookahead_and_oversized_writes_preserve_history(
    binary: Binary,
    setting: str,
) -> None:
    binary.json(
        "memory", "create", "/memories/a", "--scope", SCOPE, "--content", "before"
    )
    documents = binary.home / "user%3Aexchange/documents"
    text = (documents / "a.md").read_text()
    (documents / "b.md").write_text(text, encoding="utf-8")
    (documents / "c.md").write_text(text + "x" * 2048, encoding="utf-8")
    before = {
        str(p): p.read_bytes()
        for p in binary.home.rglob("*")
        if p.is_file() and p.name != ".store.lock"
    }
    env = {"NEOSIAN_MAX_FILE_BYTES": "1024"}
    if setting == "NEOSIAN_MAX_TEMP_BYTES":
        env[setting] = "100"
    with serve(binary, extra_env=env) as server:
        first = server.post("memory/list_documents", scope=SCOPE, limit=1)
        assert [e["path"] for e in first["entries"]] == ["a"] and first["next_cursor"]
        refused = server.request(
            "/v1/memory/list_documents",
            {"scope": SCOPE, "limit": 1, "cursor": first["next_cursor"]},
        )
        assert refused.status == 400 and "NEOSIAN_MAX_FILE_BYTES" in json.dumps(
            refused.body
        )
        refused = server.request(
            "/v1/memory/write",
            {
                "scope": SCOPE,
                "path": "a",
                "content": (
                    "x" * 2048 if setting == "NEOSIAN_MAX_FILE_BYTES" else "after"
                ),
            },
        )
        assert refused.status == 400 and setting in json.dumps(refused.body)
    assert before == {
        str(p): p.read_bytes()
        for p in binary.home.rglob("*")
        if p.is_file() and p.name != ".store.lock"
    }
