"""Literal skill packages and raw process calls, independent of Rust implementations."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.profile_support import run
from tests.contract.support import Binary

SKILL = "---\r\ndescription: Review the decision\r\n---\r\nKeep the reasoning.\r\n"
RESOURCE = "# Evidence\r\nOriginal support text.\r\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def package(path: Path, *, requires: list[dict[str, Any]] | None = None) -> Path:
    path.mkdir()
    (path / "SKILL.md").write_bytes(SKILL.encode())
    (path / "references").mkdir()
    (path / "references/reason.md").write_bytes(RESOURCE.encode())
    (path / "skill.json").write_text(
        json.dumps(
            {
                "format": 1,
                "requires": requires or [],
                "files": {"references/reason.md": sha(RESOURCE)},
                "collections": ["decisions"],
            }
        ),
        encoding="utf-8",
    )
    return path


def invoke(
    binary: Binary,
    *args: str,
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "skill",
        *args,
        *(flags if flags is not None else ["--scope", "user:skills"]),
        "--json",
        env=env,
    )
