"""Public exchange calls and independently computed bundle identifiers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.contract.knowledge_support import success
from tests.contract.profile_support import run
from tests.contract.support import Binary

SCOPE = "user:exchange"


def save(path: Path, bundle: dict[str, Any]) -> Path:
    body = {k: v for k, v in bundle.items() if k != "id"}
    bundle["id"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return path


def export(
    binary: Binary,
    name: str,
    *,
    after: Path | None = None,
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    path = binary.directory / f"{name}.json"
    success(
        run(
            binary,
            "exchange",
            "snapshot" if after is None else "delta",
            "/memories",
            str(path),
            *([] if after is None else ["--after", str(after)]),
            "--paused",
            *(flags if flags is not None else ["--scope", SCOPE]),
            "--json",
            env=env,
        )
    )
    return path


def receiver(binary: Binary) -> list[str]:
    return ["--root", str(binary.directory / "replica"), "--scope", SCOPE]


def apply(binary: Binary, path: Path, *, write: bool = True) -> dict[str, Any]:
    return success(
        binary.run(
            "exchange",
            "apply",
            str(path),
            *receiver(binary),
            *(["--write"] if write else []),
        )
    )


def files(binary: Binary) -> dict[str, bytes]:
    root = binary.directory / "replica"
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.name != ".store.lock"
    }
