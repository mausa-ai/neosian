"""Independent requests, proposal files and CLI calls for reviewed upkeep."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.knowledge_support import success
from tests.contract.profile_support import run
from tests.contract.support import Binary


def invoke(
    binary: Binary,
    *args: str,
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "upkeep",
        *args,
        *(flags if flags is not None else ["--scope", "user:upkeep"]),
        "--json",
        env=env,
    )


def save(binary: Binary, name: str, value: Any) -> Path:
    path = binary.directory / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def edit(path: str = "/memories/notes/a", **fields: Any) -> dict[str, Any]:
    return {
        "path": path,
        "operation": "replace",
        "expected_version": 1,
        "reason": "Correct against the retained evidence",
        "body": "Corrected café.\n",
        **fields,
    }


def prepare(
    binary: Binary,
    changes: list[dict[str, Any]],
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    request = save(binary, "request.json", {"format": 1, "changes": changes})
    proposal = success(invoke(binary, "prepare", str(request), flags=flags, env=env))
    return save(binary, "proposal.json", proposal), proposal


def digest(proposal: dict[str, Any]) -> str:
    payload = {key: value for key, value in proposal.items() if key != "id"}
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
