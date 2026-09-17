"""The wire's contract, pinned in literal JSON (ND, DESIGN §18.2, §18.8).

Both kits drive the wire through `RemoteStore`, which imports the codec
the server imports, so a shape only the Python decoder tolerates never
showed. Here every route is driven with a literal request body and
asserted on the literal response: the route set, the handshake, and one
scripted exchange per seam. `neosian docs wire` says what these pin;
the two must agree. The mis-typed parameters (`test_wire_types`), the
page bounds (`test_pageable_wire`) and the allowance (`test_token_allowance`)
are pinned beside this file, not repeated in it.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from neosian._foundation.server.sdk import Route

from .conftest import TOKEN, RawWire

_POSTS = {
    "/v1/memory/read",
    "/v1/memory/write",
    "/v1/memory/delete",
    "/v1/memory/rename",
    "/v1/memory/list_documents",
    "/v1/memory/versions",
    "/v1/memory/redact",
    "/v1/memory/history",
    "/v1/memory/redactions",
    "/v1/conversation/append_turn",
    "/v1/conversation/read_turns",
    "/v1/conversation/last_turn_number",
    "/v1/conversation/append_projections",
    "/v1/conversation/read_projections",
    "/v1/store/scopes",
    "/v1/store/conversations",
    "/v1/store/restore_scope",
    "/v1/store/restore_conversation",
}


def _t(seconds: int) -> str:
    """The ManualClock's n-th read, as the wire prints it."""
    return f"2026-08-19T10:00:{seconds:02d}Z"


_DOC_V1 = {
    "scope": "user:a",
    "path": "notes/a",
    "content": "x",
    "version": 1,
    "created_at": _t(0),
    "updated_at": _t(0),
    "actor": "client:default",
    "redacted": False,
    "extra": {},
}
_MESSAGE: dict[str, Any] = {
    "role": "user",
    "content": "hi",
    "reasoning": None,
    "tool_calls": [],
    "tool_call_id": None,
}
_TURN_1 = {
    "conversation_id": "c1",
    "turn": 1,
    "messages": [_MESSAGE],
    "created_at": _t(0),
    "actor": "client:default",
}


class TestTheRouteSet:
    async def test_eighteen_posts_and_two_gets(self, tmp_path: Any) -> None:
        app = await build_app(FileStore(tmp_path / "s"), token=TOKEN)
        routes = [route for route in app.routes if isinstance(route, Route)]
        by_method: dict[str, set[str]] = {"POST": set(), "GET": set()}
        for route in routes:
            for method in by_method:
                if route.methods and method in route.methods:
                    by_method[method].add(route.path)
        assert by_method["POST"] == _POSTS
        assert by_method["GET"] == {"/health", "/v1/capabilities"}
        assert len(routes) == 20

    async def test_an_unknown_route_and_a_wrong_method_are_plain(
        self, raw_wire: RawWire
    ) -> None:
        missing = await raw_wire.http.post("/v1/memory/nope", json={})
        wrong = await raw_wire.http.get("/v1/memory/read")
        assert (missing.status_code, wrong.status_code) == (404, 405)
        assert b"error" not in missing.content + wrong.content


class TestTheHandshake:
    async def test_capabilities_whole(self, raw_wire: RawWire) -> None:
        assert await raw_wire.get("/v1/capabilities") == (
            200,
            {
                "wire_version": 4,
                "neosian_version": metadata.version("neosian"),
                "backend": "FileStore",
                "supports_optimistic_concurrency": False,
                "pageable": True,
                "client": "client:default",
            },
        )

    async def test_health(self, raw_wire: RawWire) -> None:
        assert await raw_wire.get("/health") == (200, {"status": "ok"})

    async def test_a_success_is_json(self, raw_wire: RawWire) -> None:
        response = await raw_wire.http.post("/v1/store/scopes", json={})
        assert response.headers["content-type"] == "application/json"


class TestMemoryGolden:
    async def test_the_document_lifecycle(self, raw_wire: RawWire) -> None:
        write = {"scope": "user:a", "path": "notes/a", "content": "x"}
        assert await raw_wire.post("memory/write", write) == (
            200,
            {"document": _DOC_V1},
        )
        assert await raw_wire.post(
            "memory/read", {"scope": "user:a", "path": "notes/a"}
        ) == (200, {"document": _DOC_V1})
        assert await raw_wire.post(
            "memory/read", {"scope": "user:a", "path": "notes/none"}
        ) == (200, {"document": None})
        doc_v2 = {
            **_DOC_V1,
            "content": "y",
            "version": 2,
            "updated_at": _t(1),
            "actor": "client:default/me",
        }
        assert await raw_wire.post(
            "memory/write", {**write, "content": "y", "actor": "me"}
        ) == (200, {"document": doc_v2})
        assert await raw_wire.post("memory/list_documents", {"scope": "user:a"}) == (
            200,
            {
                "entries": [
                    {
                        "path": "notes/a",
                        "version": 2,
                        "created_at": _t(0),
                        "updated_at": _t(1),
                        "redacted": False,
                    }
                ],
                "next_cursor": None,
            },
        )
        assert await raw_wire.post(
            "memory/versions", {"scope": "user:a", "path": "notes/a"}
        ) == (
            200,
            {
                "versions": [
                    {
                        "path": "notes/a",
                        "version": 2,
                        "action": "modified",
                        "content": "y",
                        "actor": "client:default/me",
                        "created_at": _t(1),
                        "redacted": False,
                    },
                    {
                        "path": "notes/a",
                        "version": 1,
                        "action": "created",
                        "content": "x",
                        "actor": "client:default",
                        "created_at": _t(0),
                        "redacted": False,
                    },
                ],
                "next_cursor": None,
            },
        )
        assert await raw_wire.post(
            "memory/rename", {"scope": "user:a", "src": "notes/a", "dst": "notes/b"}
        ) == (
            200,
            {
                "document": {
                    **_DOC_V1,
                    "path": "notes/b",
                    "content": "y",
                    "created_at": _t(2),
                    "updated_at": _t(2),
                }
            },
        )
        delete = {"scope": "user:a", "path": "notes/b"}
        assert await raw_wire.post("memory/delete", delete) == (
            200,
            {"deleted": True},
        )
        assert await raw_wire.post("memory/delete", delete) == (
            200,
            {"deleted": False},
        )


class TestLedgerGolden:
    async def test_redaction_and_the_two_reads(self, raw_wire: RawWire) -> None:
        await raw_wire.post(
            "memory/write", {"scope": "user:a", "path": "notes/a", "content": "x"}
        )
        assert await raw_wire.post(
            "memory/redact", {"scope": "user:a", "path": "notes/a"}
        ) == (200, {"count": 1})
        assert await raw_wire.post("memory/redactions", {"scope": "user:a"}) == (
            200,
            {
                "redactions": [
                    {
                        "path": "notes/a",
                        "actor": "client:default",
                        "created_at": _t(1),
                        "count": 1,
                    }
                ],
                "next_cursor": None,
            },
        )
        assert await raw_wire.post("memory/history", {"scope": "user:a"}) == (
            200,
            {
                "versions": [
                    {
                        "path": "notes/a",
                        "version": 1,
                        "action": "created",
                        "content": "",
                        "actor": "client:default",
                        "created_at": _t(0),
                        "redacted": True,
                    }
                ],
                "next_cursor": None,
            },
        )

    async def test_a_scope_wide_redaction_names_no_path(
        self, raw_wire: RawWire
    ) -> None:
        for path in ("a", "b"):
            await raw_wire.post(
                "memory/write", {"scope": "user:a", "path": path, "content": "x"}
            )
        assert await raw_wire.post("memory/redact", {"scope": "user:a"}) == (
            200,
            {"count": 2},
        )
        _, body = await raw_wire.post("memory/redactions", {"scope": "user:a"})
        assert body["redactions"] == [
            {"path": None, "actor": "client:default", "created_at": _t(2), "count": 2}
        ]


class TestConversationGolden:
    async def test_the_turn_lifecycle(self, raw_wire: RawWire) -> None:
        assert await raw_wire.post(
            "conversation/append_turn",
            {"conversation_id": "c1", "messages": [{"role": "user", "content": "hi"}]},
        ) == (200, {"turn": _TURN_1})
        assert await raw_wire.post(
            "conversation/read_turns", {"conversation_id": "c1"}
        ) == (200, {"turns": [_TURN_1], "next_after": None})
        assert await raw_wire.post(
            "conversation/last_turn_number", {"conversation_id": "c1"}
        ) == (200, {"turn": 1})
        assert await raw_wire.post(
            "conversation/last_turn_number", {"conversation_id": "never"}
        ) == (200, {"turn": 0})
        assert await raw_wire.post(
            "conversation/append_projections",
            {
                "conversation_id": "c1",
                "entries": [{"turn": 1, "kind": "log", "text": "t"}],
            },
        ) == (200, {})
        assert await raw_wire.post(
            "conversation/read_projections", {"conversation_id": "c1"}
        ) == (
            200,
            {
                "entries": [{"turn": 1, "kind": "log", "text": "t", "span": 1}],
                "next_after": None,
            },
        )


class TestStoreGolden:
    async def test_the_listings(self, raw_wire: RawWire) -> None:
        await raw_wire.post(
            "memory/write", {"scope": "user:a", "path": "notes/a", "content": "x"}
        )
        await raw_wire.post(
            "conversation/append_turn",
            {"conversation_id": "c1", "messages": [_MESSAGE]},
        )
        assert await raw_wire.post("store/scopes", {}) == (
            200,
            {"scopes": ["user:a"]},
        )
        assert await raw_wire.post("store/conversations", {}) == (
            200,
            {"conversations": ["c1"]},
        )

    async def test_a_scope_restores_verbatim(self, raw_wire: RawWire) -> None:
        document = {**_DOC_V1, "scope": "user:r", "actor": "conv:x#1"}
        version = {
            "path": "notes/a",
            "version": 1,
            "action": "created",
            "content": "x",
            "actor": "conv:x#1",
            "created_at": _t(0),
            "redacted": False,
        }
        redaction = {
            "path": "old",
            "actor": "conv:x#2",
            "created_at": _t(0),
            "count": 1,
        }
        assert await raw_wire.post(
            "store/restore_scope",
            {
                "scope": "user:r",
                "documents": [document],
                "versions": [version],
                "redactions": [redaction],
            },
        ) == (200, {})
        assert await raw_wire.post(
            "memory/read", {"scope": "user:r", "path": "notes/a"}
        ) == (200, {"document": document})
        assert await raw_wire.post("memory/history", {"scope": "user:r"}) == (
            200,
            {"versions": [version], "next_cursor": None},
        )
        assert await raw_wire.post("memory/redactions", {"scope": "user:r"}) == (
            200,
            {"redactions": [redaction], "next_cursor": None},
        )

    async def test_a_conversation_restores_verbatim(self, raw_wire: RawWire) -> None:
        turn = {**_TURN_1, "conversation_id": "r1", "actor": None}
        projection = {"turn": 1, "kind": "digest", "text": "d", "span": 1}
        assert await raw_wire.post(
            "store/restore_conversation",
            {"conversation_id": "r1", "turns": [turn], "projections": [projection]},
        ) == (200, {})
        assert await raw_wire.post(
            "conversation/read_turns", {"conversation_id": "r1"}
        ) == (200, {"turns": [turn], "next_after": None})
        assert await raw_wire.post(
            "conversation/read_projections", {"conversation_id": "r1"}
        ) == (200, {"entries": [projection], "next_after": None})


class TestThePage:
    def test_wire_md_names_every_route(self) -> None:
        # The page is the contract and these pins are its evidence: a
        # route the page does not name is a route the contract lost.
        from neosian._foundation.shared.docs_assets import load_page

        page = load_page("wire")
        assert page is not None
        body = page.body
        for path in _POSTS:
            assert f"`{path.removeprefix('/v1/')}`" in body, path
        assert "eighteen" in body and "`WIRE_VERSION`" in body
