"""Reference FileStore cursor decoding; custom services keep their own encoding."""

import base64
import json
from typing import Any

import pytest

from tests.contract.file.support import SCOPE, snapshot
from tests.contract.storage.support import refused
from tests.contract.support import Binary, serve


def cursor(parts: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts).encode()).decode()


@pytest.mark.parametrize(
    ("route", "parts"),
    [
        ("list_documents", ["list", 1]),
        ("list_documents", ["versions", 1]),
        ("versions", ["versions", True]),
        ("versions", ["versions", 1.5]),
        ("versions", ["versions", "2"]),
        ("history", ["history", "2026-08-19T10:00:00", "notes/fact", 1]),
        ("history", ["history", "later", "notes/fact", 1]),
        ("history", ["history", "2026-08-19T10:00:00Z", "notes/fact", True]),
        ("redactions", ["trail", True]),
        ("redactions", ["trail", "1"]),
    ],
)
def test_malformed_keysets_are_named_refusals_not_server_failures(
    planted: Binary, route: str, parts: list[Any]
) -> None:
    before = snapshot(planted.home)
    with serve(planted) as server:
        body = {"scope": SCOPE, "cursor": cursor(parts)}
        if route == "versions":
            body["path"] = "notes/fact"
        reply = server.request("/v1/memory/" + route, body)
        refused(reply, "value_error")
        assert reply.body["error"]["message"].startswith("malformed ")
        assert server.request("/health", method="GET").status == 200
        assert snapshot(planted.home) == before


@pytest.mark.parametrize("version", [-1, 0])
def test_signed_history_key_keeps_its_ordering_without_panicking(
    planted: Binary, version: int
) -> None:
    # A reference keyset decoder accepts integers, including signed ones.
    # Equal-stamp rows with the same path cannot follow a nonpositive version.
    with serve(planted) as server:
        page = server.post(
            "memory/history",
            scope=SCOPE,
            cursor=cursor(["history", "2026-08-19T10:00:00Z", "notes/fact", version]),
        )
        assert page == {"versions": [], "next_cursor": None}
        assert (
            server.post("memory/read", scope=SCOPE, path="notes/fact")["document"][
                "version"
            ]
            == 2
        )
