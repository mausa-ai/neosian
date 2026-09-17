"""The shapes a foreign client would send and `RemoteStore` never does
(ND, DESIGN §18.2, §18.3): absent against null, the message object's
tolerances, nested parameters typed at the door, timestamp spellings,
the envelopes whole, the archive's required keys, and the body itself.
Each probe asserts the literal outcome `neosian docs wire` states.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import RawWire

_SCOPE = "user:a"
_WRITE = {"scope": _SCOPE, "path": "notes/a", "content": "x"}
_READ = {"scope": _SCOPE, "path": "notes/a"}


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def _turn(raw_wire: RawWire, message: dict[str, Any]) -> tuple[int, Any]:
    return await raw_wire.post(
        "conversation/append_turn", {"conversation_id": "c", "messages": [message]}
    )


def _stored(body: Any) -> dict[str, Any]:
    message: dict[str, Any] = body["turn"]["messages"][0]
    return message


class TestOptionals:
    async def test_absent_and_null_are_the_same_parameter(
        self, raw_wire: RawWire
    ) -> None:
        nulls = {**_WRITE, "actor": None, "expected_version": None}
        _, first = await raw_wire.post("memory/write", nulls)
        assert first["document"]["actor"] == "client:default"
        _, listed = await raw_wire.post(
            "memory/list_documents", {"scope": _SCOPE, "prefix": None, "limit": None}
        )
        _, bare = await raw_wire.post("memory/list_documents", {"scope": _SCOPE})
        assert listed == bare
        _, history = await raw_wire.post(
            "memory/history",
            {"scope": _SCOPE, "since": None, "cursor": None, "limit": None},
        )
        assert history == (await raw_wire.post("memory/history", {"scope": _SCOPE}))[1]

    async def test_unknown_top_level_keys_are_ignored(self, raw_wire: RawWire) -> None:
        await raw_wire.post("memory/write", _WRITE)
        status, body = await raw_wire.post("memory/read", {**_READ, "junk": 1})
        assert (status, body["document"]["content"]) == (200, "x")
        assert await raw_wire.post(
            "store/scopes", {"scope": "user:zzz", "junk": 1}
        ) == (
            200,
            {"scopes": [_SCOPE]},
        )


class TestMessageShapes:
    async def test_extra_is_omitted_when_empty(self, raw_wire: RawWire) -> None:
        for message in (
            {"role": "user", "content": "hi"},
            {"role": "user", "extra": {}},
        ):
            _, body = await _turn(raw_wire, message)
            assert "extra" not in _stored(body)
        _, body = await _turn(raw_wire, {"role": "user", "extra": {"k": 1}})
        assert _stored(body)["extra"] == {"k": 1}

    async def test_unknown_keys_inside_a_message_are_dropped(
        self, raw_wire: RawWire
    ) -> None:
        _, body = await _turn(raw_wire, {"role": "user", "content": "hi", "name": "b"})
        assert set(_stored(body)) == {
            "role",
            "content",
            "reasoning",
            "tool_calls",
            "tool_call_id",
        }

    async def test_the_content_forms(self, raw_wire: RawWire) -> None:
        _, body = await _turn(raw_wire, {"role": "assistant", "content": None})
        assert _stored(body)["content"] is None
        blocks = [
            {"type": "text", "text": "hi"},
            {"type": "image", "url": "https://x/y.png"},
        ]
        _, body = await _turn(raw_wire, {"role": "user", "content": blocks})
        assert _stored(body)["content"] == [
            blocks[0],
            {
                "type": "image",
                "media_type": None,
                "data": None,
                "url": "https://x/y.png",
            },
        ]
        status, body = await _turn(
            raw_wire, {"role": "user", "content": [{"type": "bogus"}]}
        )
        assert (status, body) == (
            400,
            _error("value_error", "Unknown content block type: 'bogus'"),
        )

    async def test_a_tool_call_round_trips(self, raw_wire: RawWire) -> None:
        call = {"id": "c1", "name": "memory", "arguments": {"command": "view"}}
        _, body = await _turn(raw_wire, {"role": "assistant", "tool_calls": [call]})
        assert _stored(body)["tool_calls"] == [call]
        _, body = await _turn(
            raw_wire, {"role": "assistant", "tool_calls": [{"id": "c2", "name": "m"}]}
        )
        assert _stored(body)["tool_calls"] == [
            {"id": "c2", "name": "m", "arguments": {}}
        ]
        _, body = await _turn(raw_wire, {"role": "tool", "tool_call_id": ""})
        assert _stored(body)["tool_call_id"] is None

    @pytest.mark.parametrize(
        ("call", "parameter"),
        [
            ({"id": "c1", "name": "m", "arguments": '{"a": 1}'}, "arguments"),
            ({"id": 5, "name": "m", "arguments": {}}, "id"),
            ({"id": "c1", "name": 3, "arguments": {}}, "name"),
        ],
    )
    async def test_a_tool_call_is_typed_at_the_door(
        self, raw_wire: RawWire, call: dict[str, Any], parameter: str
    ) -> None:
        status, body = await _turn(
            raw_wire, {"role": "assistant", "tool_calls": [call]}
        )
        assert status == 400
        assert body["error"]["code"] == "value_error"
        assert repr(parameter) in body["error"]["message"]

    async def test_the_role_and_the_message_list(self, raw_wire: RawWire) -> None:
        status, body = await _turn(raw_wire, {"role": "robot"})
        assert (status, body["error"]["code"]) == (400, "value_error")
        assert await raw_wire.post(
            "conversation/append_turn", {"conversation_id": "c", "messages": []}
        ) == (400, _error("value_error", "a turn must carry at least one message"))
        assert await raw_wire.post(
            "conversation/append_turn",
            {"conversation_id": "c", "messages": [{"content": "no role"}]},
        ) == (400, _error("value_error", "malformed request: 'role'"))


class TestProjectionShapes:
    async def _append(
        self, raw_wire: RawWire, entry: dict[str, Any]
    ) -> tuple[int, Any]:
        await _turn(raw_wire, {"role": "user", "content": "hi"})
        return await raw_wire.post(
            "conversation/append_projections",
            {"conversation_id": "c", "entries": [entry]},
        )

    @pytest.mark.parametrize("entry", [{}, {"span": None}])
    async def test_span_absent_or_null_is_one(
        self, raw_wire: RawWire, entry: dict[str, Any]
    ) -> None:
        entry = {"turn": 1, "kind": "log", "text": "t", **entry}
        assert await self._append(raw_wire, entry) == (200, {})
        _, body = await raw_wire.post(
            "conversation/read_projections", {"conversation_id": "c"}
        )
        assert body["entries"] == [{"turn": 1, "kind": "log", "text": "t", "span": 1}]

    @pytest.mark.parametrize(
        ("entry", "parameter"),
        [
            ({"turn": 1, "kind": "log", "text": "t", "span": "1"}, "span"),
            ({"turn": "1", "kind": "log", "text": "t"}, "turn"),
            ({"turn": 1, "kind": "log", "text": 5}, "text"),
            ({"turn": 1, "kind": "log"}, "text"),
        ],
    )
    async def test_an_entry_is_typed_at_the_door(
        self, raw_wire: RawWire, entry: dict[str, Any], parameter: str
    ) -> None:
        status, body = await self._append(raw_wire, entry)
        assert status == 400
        assert body["error"]["code"] == "value_error"
        assert repr(parameter) in body["error"]["message"]

    async def test_the_kind_is_the_store_s_check(self, raw_wire: RawWire) -> None:
        status, body = await self._append(
            raw_wire, {"turn": 1, "kind": "bogus", "text": "t"}
        )
        assert (status, body["error"]["code"]) == (400, "value_error")
        assert "kind must be one of" in body["error"]["message"]


class TestTimestamps:
    async def test_since_takes_any_offset(self, raw_wire: RawWire) -> None:
        for path in ("a", "b"):
            await raw_wire.post(
                "memory/write", {"scope": _SCOPE, "path": path, "content": "x"}
            )
        for since in (
            "2026-08-19T10:00:01Z",
            "2026-08-19T10:00:01+00:00",
            "2026-08-19T12:00:01+02:00",
        ):
            _, body = await raw_wire.post(
                "memory/history", {"scope": _SCOPE, "since": since}
            )
            assert [row["path"] for row in body["versions"]] == ["b"], since


class TestPagingEdges:
    async def test_the_conversation_edges(self, raw_wire: RawWire) -> None:
        await _turn(raw_wire, {"role": "user", "content": "hi"})
        read = "conversation/read_turns"
        assert await raw_wire.post(read, {"conversation_id": "c", "limit": 0}) == (
            200,
            {"turns": [], "next_after": None},
        )
        _, bare = await raw_wire.post(read, {"conversation_id": "c"})
        for after in (0, None):
            _, body = await raw_wire.post(
                read, {"conversation_id": "c", "after": after}
            )
            assert body == bare
        assert await raw_wire.post(read, {"conversation_id": "c", "after": -1}) == (
            400,
            _error("value_error", "after must be >= 0, got -1"),
        )
        assert await raw_wire.post(
            "memory/history", {"scope": _SCOPE, "limit": -1}
        ) == (
            400,
            _error("value_error", "limit must be >= 0"),
        )


class TestEnvelopes:
    async def test_the_conflicts_whole(self, raw_wire: RawWire) -> None:
        await raw_wire.post("memory/write", _WRITE)
        assert await raw_wire.post(
            "memory/write", {**_WRITE, "expected_version": 5}
        ) == (
            400,
            _error(
                "memory_conflict",
                "Memory conflict on 'notes/a' in scope 'user:a': version_mismatch",
                scope=_SCOPE,
                path="notes/a",
                reason="version_mismatch",
                expected_version=5,
                actual_version=1,
            ),
        )
        status, body = await raw_wire.post(
            "memory/write", {**_WRITE, "path": "new", "expected_version": 1}
        )
        assert (status, body["error"]["details"]["reason"]) == (400, "document_absent")
        assert body["error"]["details"]["actual_version"] is None
        status, body = await raw_wire.post(
            "memory/rename", {"scope": _SCOPE, "src": "notes/a", "dst": "notes/a"}
        )
        assert body["error"]["details"] == {
            "scope": _SCOPE,
            "path": "notes/a",
            "reason": "destination_exists",
            "expected_version": None,
            "actual_version": None,
        }

    async def test_not_found_and_invalid_whole(self, raw_wire: RawWire) -> None:
        assert await raw_wire.post(
            "memory/rename", {"scope": _SCOPE, "src": "nope", "dst": "x"}
        ) == (
            400,
            _error(
                "memory_document_not_found",
                "Memory document not found: 'nope' in scope 'user:a'",
                scope=_SCOPE,
                path="nope",
            ),
        )
        assert await raw_wire.post("memory/read", {"scope": "bad", "path": "x"}) == (
            400,
            _error(
                "memory_scope_invalid",
                "Invalid memory scope 'bad': does not match <type>:<id>[/...]",
                scope="bad",
                reason="does not match <type>:<id>[/...]",
            ),
        )

    async def test_an_occupied_restore_target(self, raw_wire: RawWire) -> None:
        await raw_wire.post("memory/write", _WRITE)
        assert await raw_wire.post(
            "store/restore_scope",
            {"scope": _SCOPE, "documents": [], "versions": [], "redactions": []},
        ) == (
            400,
            _error(
                "memory_conflict",
                "Memory conflict in scope 'user:a': target_occupied",
                scope=_SCOPE,
                path=None,
                reason="target_occupied",
                expected_version=None,
                actual_version=None,
            ),
        )


class TestArchiveTolerance:
    _DOC = {
        "scope": "user:r",
        "path": "p",
        "content": "x",
        "version": 1,
        "created_at": "2026-08-19T10:00:00Z",
        "updated_at": "2026-08-19T10:00:00Z",
        "actor": "a",
        "redacted": False,
        "extra": {},
    }

    async def _restore(self, raw_wire: RawWire, **parts: Any) -> tuple[int, Any]:
        body = {"scope": "user:r", "documents": [], "versions": [], "redactions": []}
        return await raw_wire.post("store/restore_scope", {**body, **parts})

    async def test_a_document_needs_every_key_but_extra(
        self, raw_wire: RawWire
    ) -> None:
        without_extra = {k: v for k, v in self._DOC.items() if k != "extra"}
        assert await self._restore(raw_wire, documents=[without_extra]) == (200, {})
        without_actor = {k: v for k, v in self._DOC.items() if k != "actor"}
        assert await self._restore(
            raw_wire, scope="user:s", documents=[without_actor]
        ) == (
            400,
            _error("value_error", "malformed request: 'actor'"),
        )

    async def test_a_version_row_needs_redacted(self, raw_wire: RawWire) -> None:
        row = {
            "path": "p",
            "version": 1,
            "action": "created",
            "content": "x",
            "actor": "a",
            "created_at": "2026-08-19T10:00:00Z",
        }
        assert await self._restore(raw_wire, versions=[row]) == (
            400,
            _error("value_error", "malformed request: 'redacted'"),
        )

    async def test_a_turn_needs_no_actor(self, raw_wire: RawWire) -> None:
        turn = {
            "conversation_id": "r",
            "turn": 1,
            "messages": [{"role": "user", "content": "hi"}],
            "created_at": "2026-08-19T10:00:00Z",
        }
        assert await raw_wire.post(
            "store/restore_conversation",
            {"conversation_id": "r", "turns": [turn], "projections": []},
        ) == (200, {})
        _, body = await raw_wire.post(
            "conversation/read_turns", {"conversation_id": "r"}
        )
        assert body["turns"][0]["actor"] is None


class TestBodyShapes:
    @pytest.mark.parametrize("content", [b"[]", b"null", b'"x"'])
    async def test_a_non_object_body(self, raw_wire: RawWire, content: bytes) -> None:
        status, text = await raw_wire.send("memory/read", content)
        assert status == 400
        assert '"request body must be an object"' in text

    @pytest.mark.parametrize("content", [b"", b"{"])
    async def test_a_non_json_body(self, raw_wire: RawWire, content: bytes) -> None:
        status, text = await raw_wire.send("memory/read", content)
        assert status == 400
        assert '"request body must be JSON"' in text

    async def test_the_content_type_is_not_read(self, raw_wire: RawWire) -> None:
        status, text = await raw_wire.send(
            "memory/read",
            b'{"scope": "user:a", "path": "x"}',
            **{"Content-Type": "text/plain"},
        )
        assert (status, text) == (200, '{"document":null}')
