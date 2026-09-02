"""The wire validates every parameter server-side: a mis-typed value is the
§18 envelope at 400 — never a JSON number written verbatim as document
content, never a bare 500 — and a body over the ceiling is 413 in the same
envelope, which `RemoteStore` raises as the ABCs' own `ValueError`."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.server import routes
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.server.sdk import Starlette

from .conftest import BASE_URL, TOKEN

_MESSAGE = {"role": "user", "content": "hi"}


@pytest.fixture
def backing(tmp_path: Any) -> FileStore:
    return FileStore(tmp_path / "mem")


@pytest.fixture
async def app(backing: FileStore) -> Starlette:
    return await build_app(backing, token=TOKEN)


@pytest.fixture
async def client(app: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as bare:
        yield bare


def _doc(**overrides: Any) -> dict[str, Any]:
    return {"scope": "user:a", "path": "a", "content": "x", **overrides}


_WRONG_TYPES: list[tuple[str, dict[str, Any], str]] = [
    ("memory/read", {"scope": 5, "path": "a"}, "scope"),
    ("memory/read", {"scope": "user:a", "path": ["a"]}, "path"),
    ("memory/write", _doc(content=5), "content"),
    ("memory/write", _doc(actor=7), "actor"),
    ("memory/write", _doc(expected_version="1"), "expected_version"),
    ("memory/write", _doc(expected_version=True), "expected_version"),
    ("memory/delete", {"scope": "user:a", "path": 1}, "path"),
    ("memory/rename", {"scope": "user:a", "src": "a", "dst": 1}, "dst"),
    ("memory/list_documents", {"scope": "user:a", "prefix": 5}, "prefix"),
    ("memory/versions", {"scope": "user:a", "path": "a", "limit": "x"}, "limit"),
    ("memory/versions", {"scope": "user:a", "path": "a", "limit": False}, "limit"),
    ("memory/redact", {"scope": "user:a", "path": 3}, "path"),
    (
        "conversation/append_turn",
        {"conversation_id": 1, "messages": []},
        "conversation_id",
    ),
    ("conversation/append_turn", {"conversation_id": "c1", "messages": 5}, "messages"),
    (
        "conversation/append_turn",
        {"conversation_id": "c1", "messages": [5]},
        "messages",
    ),
    ("conversation/read_turns", {"conversation_id": "c1", "after": True}, "after"),
    ("conversation/read_turns", {"conversation_id": "c1", "limit": "x"}, "limit"),
    ("conversation/last_turn_number", {"conversation_id": {}}, "conversation_id"),
    (
        "conversation/append_projections",
        {"conversation_id": "c1", "entries": 5},
        "entries",
    ),
    (
        "conversation/append_projections",
        {"conversation_id": "c1", "entries": ["x"]},
        "entries",
    ),
    ("conversation/read_projections", {"conversation_id": "c1", "after": "0"}, "after"),
]


class TestWrongTypes:
    @pytest.mark.parametrize(("endpoint", "payload", "parameter"), _WRONG_TYPES)
    async def test_is_the_envelope_at_400(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: dict[str, Any],
        parameter: str,
    ) -> None:
        response = await client.post(f"/v1/{endpoint}", json=payload)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "value_error"
        assert repr(parameter) in error["message"]
        assert error["details"] == {}

    async def test_a_numeric_content_is_never_written(
        self, client: httpx.AsyncClient, backing: FileStore
    ) -> None:
        # The reproduced IN-1: `{"content": 5}` used to land verbatim and
        # read back as "5", with an int in the sidecar behind a str field.
        response = await client.post("/v1/memory/write", json=_doc(content=5))
        assert response.status_code == 400
        assert await backing.read("user:a", "a") is None
        assert await backing.versions("user:a", "a") == ()

    async def test_a_missing_parameter_is_named(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/v1/memory/read", json={"scope": "user:a"})
        assert response.status_code == 400
        assert response.json()["error"]["message"] == "missing parameter: 'path'"

    async def test_a_malformed_nested_object_is_still_400(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/v1/conversation/append_turn",
            json={"conversation_id": "c1", "messages": [{"content": "no role"}]},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "value_error"
        assert "malformed request" in response.json()["error"]["message"]

    async def test_well_typed_optionals_still_default(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await client.post("/v1/memory/write", json=_doc())).status_code == 200
        listed = await client.post(
            "/v1/memory/list_documents", json={"scope": "user:a"}
        )
        assert [e["path"] for e in listed.json()["entries"]] == ["a"]
        rows = await client.post(
            "/v1/memory/versions", json={"scope": "user:a", "path": "a"}
        )
        assert len(rows.json()["versions"]) == 1


class TestBodyCeiling:
    async def test_a_declared_oversize_body_is_413(
        self,
        client: httpx.AsyncClient,
        backing: FileStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(routes, "MAX_REQUEST_BYTES", 256)
        response = await client.post("/v1/memory/write", json=_doc(content="x" * 300))
        assert response.status_code == 413
        error = response.json()["error"]
        assert error["code"] == "value_error"
        assert "exceeds 256 bytes" in error["message"]
        assert await backing.read("user:a", "a") is None

    async def test_a_chunked_oversize_body_is_413(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No Content-Length to short-circuit on: the ceiling bites while
        # the body streams in.
        monkeypatch.setattr(routes, "MAX_REQUEST_BYTES", 256)

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b'{"scope": "user:a", "path": "a", "content": "' + b"x" * 100
            yield b'"}'

        response = await client.post(
            "/v1/memory/write",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413

    async def test_a_body_under_the_ceiling_passes(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(routes, "MAX_REQUEST_BYTES", 256)
        response = await client.post("/v1/memory/write", json=_doc(content="x" * 100))
        assert response.status_code == 200

    async def test_remote_store_raises_value_error(
        self, app: Starlette, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(routes, "MAX_REQUEST_BYTES", 256)
        remote = await RemoteStore.connect(
            BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
        )
        async with remote:
            with pytest.raises(ValueError, match="exceeds 256 bytes"):
                await remote.write("user:a", "a", "x" * 300)
