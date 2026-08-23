"""`RemoteStore.connect` — the capability handshake (DESIGN §18).

Capability is transmitted, never claimed: the returned instance's
**class** carries the backend's `supports_optimistic_concurrency`,
because both conformance kits read the flag off `type(store)` — an
instance attribute would be invisible to them. Wire-version skew and a
rejected token refuse loudly, before any store call.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian import RemoteStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from neosian._foundation.server.wire import WIRE_VERSION
from neosian._foundation.shared.exceptions import ConfigurationError

from .conftest import BASE_URL, TOKEN


def _capabilities_app(payload: dict[str, Any]) -> httpx.MockTransport:
    """A server that answers only the handshake, with a scripted payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


class TestCapabilityMirroring:
    async def test_filestore_backend_mirrors_false_on_the_class(
        self, tmp_path: Path
    ) -> None:
        app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
        store = await RemoteStore.connect(
            BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
        )
        try:
            # `type(store)` is what the conformance kits read.
            assert type(store).supports_optimistic_concurrency is False
        finally:
            await store.aclose()

    async def test_an_arbitrating_backend_mirrors_true_on_the_class(self) -> None:
        transport = _capabilities_app(
            {
                "wire_version": WIRE_VERSION,
                "neosian_version": "0.0.0",
                "backend": "PostgresStore",
                "supports_optimistic_concurrency": True,
            }
        )
        store = await RemoteStore.connect(BASE_URL, token=TOKEN, transport=transport)
        try:
            assert type(store).supports_optimistic_concurrency is True
            assert isinstance(store, RemoteStore)
        finally:
            await store.aclose()

    def test_the_plain_constructor_keeps_the_conservative_default(self) -> None:
        store = RemoteStore(BASE_URL, token=TOKEN)
        assert type(store).supports_optimistic_concurrency is False


class TestRefusals:
    async def test_wire_version_skew_refuses(self) -> None:
        transport = _capabilities_app(
            {
                "wire_version": WIRE_VERSION + 1,
                "neosian_version": "9.9.9",
                "backend": "FileStore",
                "supports_optimistic_concurrency": False,
            }
        )
        with pytest.raises(ConfigurationError, match="wire version skew"):
            await RemoteStore.connect(BASE_URL, token=TOKEN, transport=transport)

    async def test_a_rejected_token_names_the_env_key(self, tmp_path: Path) -> None:
        app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
        with pytest.raises(ConfigurationError, match="NEOSIAN_SERVE_TOKEN"):
            await RemoteStore.connect(
                BASE_URL, token="wrong", transport=httpx.ASGITransport(app=app)
            )

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"url": "ftp://x", "token": "t"}, "http"),
            ({"url": BASE_URL, "token": ""}, "bearer token"),
            ({"url": BASE_URL, "token": "t", "timeout": 0.0}, "timeout"),
        ],
    )
    def test_the_constructor_validates_purely(
        self, kwargs: dict[str, Any], match: str
    ) -> None:
        url = kwargs.pop("url")
        with pytest.raises(ConfigurationError, match=match):
            RemoteStore(url, **kwargs)


class TestLifetime:
    @pytest.fixture
    async def store(self, tmp_path: Path) -> AsyncIterator[RemoteStore]:
        app = await build_app(FileStore(tmp_path / "mem"), token=TOKEN)
        yield RemoteStore(BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app))

    async def test_aclose_is_idempotent(self, store: RemoteStore) -> None:
        await store.write("user:a", "x", "body")
        await store.aclose()
        await store.aclose()

    async def test_the_client_reopens_after_close(self, store: RemoteStore) -> None:
        await store.write("user:a", "x", "body")
        await store.aclose()
        document = await store.read("user:a", "x")
        assert document is not None
        assert document.content == "body"
        await store.aclose()

    async def test_async_with_closes(self, store: RemoteStore) -> None:
        async with store as opened:
            await opened.write("user:a", "x", "body")
        assert store._client is None  # noqa: SLF001 — lifetime pin
