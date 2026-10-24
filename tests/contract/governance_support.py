"""Independent governance records and digest recipe for raw contract tests."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from tests.contract.knowledge_support import invoke, marked, success, unpack
from tests.contract.support import Binary
from tests.contract.upkeep_support import save

KEY = "neosian_governance"
SCOPE = "org:governance"


def review_hash(scope: str, path: str, text: str) -> str:
    metadata, body = unpack(text)
    del metadata["attributes"][KEY]["review"]
    raw = json.dumps(
        [1, scope, path, metadata, body],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def governed(scope: str, path: str, body: str = "needle", **fields: Any) -> str:
    record: dict[str, Any] = {
        "format": 1,
        "kind": "procedure",
        "owners": ["PRIVATE_REVIEW_LABEL"],
        "state": "approved",
        **fields,
    }
    if record["state"] == "approved":
        record.setdefault(
            "review",
            {"by": "human:reviewer", "at": "2000-01-01T00:00:00Z", "sha256": ""},
        )
        raw = marked(body, attributes={KEY: record})
        record["review"]["sha256"] = review_hash(scope, path, raw)
    return marked(body, attributes={KEY: record})


def proposal(
    binary: Binary,
    *,
    path: str = "/memories/notes/a",
    version: int = 1,
    flags: list[str] | None = None,
    env: dict[str, str] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    change = {
        "format": 1,
        "kind": "decision",
        "owners": ["PRIVATE_REVIEW_LABEL"],
        "state": "approved",
        "reviewer": "human:reviewer",
        **fields,
    }
    file = save(binary, "review.json", change)
    return success(
        invoke(
            binary,
            "govern",
            path,
            "--file",
            str(file),
            "--expect-version",
            str(version),
            "--reason",
            "Review current procedure",
            flags=flags if flags is not None else ["--scope", SCOPE],
            env=env,
        )
    )


def changed(text: str, **fields: Any) -> str:
    metadata, body = unpack(text)
    metadata = copy.deepcopy(metadata)
    metadata["attributes"][KEY].update(fields)
    return marked(body, **metadata)
