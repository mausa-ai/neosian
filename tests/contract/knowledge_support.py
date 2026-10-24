"""Literal knowledge fixtures and raw CLI calls, independent of Rust codecs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.profile_support import run
from tests.contract.support import Binary


def marked(body: str, **metadata: Any) -> str:
    data = {
        "format": 1,
        "collection": "notes",
        "attributes": {},
        "references": [],
        "sources": [],
        **metadata,
    }
    return "<!-- neosian-knowledge:1\n" + json.dumps(data) + "\n-->\n" + body


def unpack(content: str) -> tuple[dict[str, Any], str]:
    assert content.startswith("<!-- neosian-knowledge:1\n")
    header, body = content.removeprefix("<!-- neosian-knowledge:1\n").split(
        "\n-->\n", 1
    )
    return json.loads(header), body


def invoke(
    binary: Binary,
    *args: str,
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "knowledge",
        *args,
        *(flags if flags is not None else ["--scope", "user:knowledge"]),
        "--json",
        env=env,
    )


def success(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stdout + result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def bundle(directory: Path, text: str, **changes: Any) -> Path:
    directory.mkdir()
    (directory / "A document.md").write_bytes(text.encode())
    manifest: dict[str, Any] = {
        "format": 1,
        "collection": None,
        "documents": [
            {
                "path": "a",
                "file": "A document.md",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "origin": {
                    "scope": "user:original",
                    "path": "prior/a",
                    "version": 17,
                    "created_at": "2026-10-20T01:02:03Z",
                    "updated_at": "2026-10-21T04:05:06Z",
                    "actor": "opaque original actor",
                    "extra": {"foreign": {"nested": [1, None, "preserved"]}},
                },
            }
        ],
        **changes,
    }
    (directory / "knowledge.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory
