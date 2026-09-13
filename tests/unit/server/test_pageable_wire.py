"""Paged listings over the wire (NQ2 slice C, §18.2): the `Pageable`
suite through RemoteStore, every listing route bounded at `PAGE` with its
continuation, and a host backend that cannot page answering whole."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.server import paging
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.shared.exceptions import ConfigurationError
from tests.support.clock import ManualClock
from tests.unit.memory.paging import SCOPE, FrozenClock, PagingSuite, seed

from .conftest import BASE_URL, TOKEN

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.asyncio


async def _connect(backing: FileStore) -> RemoteStore:
    app = await build_app(backing, token=TOKEN)
    return await RemoteStore.connect(
        BASE_URL, token=TOKEN, transport=httpx.ASGITransport(app=app)
    )


class TestRemotePaging(PagingSuite):
    @pytest.fixture
    async def store(self, tmp_path: Path) -> AsyncIterator[RemoteStore]:
        remote = await _connect(FileStore(tmp_path / "backing", clock=ManualClock()))
        try:
            yield remote
        finally:
            await remote.aclose()


class TestRemotePagingOnTies(TestRemotePaging):
    @pytest.fixture
    async def store(self, tmp_path: Path) -> AsyncIterator[RemoteStore]:
        remote = await _connect(FileStore(tmp_path / "backing", clock=FrozenClock()))
        try:
            yield remote
        finally:
            await remote.aclose()


@pytest.fixture
async def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The raw routes over a seeded FileStore, two rows a page."""
    monkeypatch.setattr(paging, "PAGE", 2)
    backing = FileStore(tmp_path / "backing", clock=ManualClock())
    await seed(backing)
    app = await build_app(backing, token=TOKEN)
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
        transport=httpx.ASGITransport(app=app),
    ) as client:

        async def post(route: str, **payload: Any) -> Any:
            response = await client.post(f"/v1/{route}", json=payload)
            return response.status_code, response.json()

        yield post


class TestTheRoutesAreBounded:
    @pytest.mark.parametrize(
        ("route", "key"),
        [
            ("memory/list_documents", "entries"),
            ("memory/history", "versions"),
            ("memory/redactions", "redactions"),
        ],
    )
    async def test_no_limit_answers_a_page_and_its_cursor(
        self, raw: Any, route: str, key: str
    ) -> None:
        status, body = await raw(route, scope=SCOPE, limit=None)
        assert status == 200
        assert len(body[key]) == 2
        assert isinstance(body["next_cursor"], str)
        status, rest = await raw(route, scope=SCOPE, cursor=body["next_cursor"])
        assert status == 200
        assert body[key][-1] not in rest[key]

    async def test_versions_keeps_its_default_and_pages_past_it(self, raw: Any) -> None:
        status, body = await raw("memory/versions", scope=SCOPE, path="notes/1")
        assert (status, len(body["versions"])) == (200, 2)
        _, rest = await raw(
            "memory/versions", scope=SCOPE, path="notes/1", cursor=body["next_cursor"]
        )
        assert ([row["version"] for row in rest["versions"]], rest["next_cursor"]) == (
            [1],
            None,
        )

    async def test_a_limit_within_a_page_is_the_abc_answer(self, raw: Any) -> None:
        status, body = await raw("memory/history", scope=SCOPE, limit=1)
        assert (status, len(body["versions"])) == (200, 1)
        assert isinstance(body["next_cursor"], str)  # more rows follow
        _, empty = await raw("memory/history", scope=SCOPE, limit=0)
        assert empty == {"versions": [], "next_cursor": None}

    async def test_the_conversation_reads_answer_next_after(self, raw: Any) -> None:
        for n in range(5):
            _, _ = await raw(
                "conversation/append_turn",
                conversation_id="c",
                messages=[{"role": "user", "content": f"t{n}"}],
            )
        _, body = await raw("conversation/read_turns", conversation_id="c")
        assert [turn["turn"] for turn in body["turns"]] == [1, 2]
        assert body["next_after"] == 2
        _, exact = await raw("conversation/read_turns", conversation_id="c", limit=2)
        assert exact["next_after"] is None
        _, rest = await raw("conversation/read_turns", conversation_id="c", after=4)
        assert ([t["turn"] for t in rest["turns"]], rest["next_after"]) == ([5], None)

    @pytest.mark.parametrize("cursor", [7, True, ["x"]])
    async def test_a_cursor_is_a_string(self, raw: Any, cursor: object) -> None:
        status, body = await raw("memory/history", scope=SCOPE, cursor=cursor)
        assert (status, body["error"]["code"]) == (400, "value_error")

    async def test_a_foreign_cursor_is_a_value_error(self, raw: Any) -> None:
        _, body = await raw("memory/history", scope=SCOPE, limit=1)
        status, refused = await raw(
            "memory/redactions", scope=SCOPE, cursor=body["next_cursor"]
        )
        assert (status, refused["error"]["code"]) == (400, "value_error")


class TestABackendThatCannotPage:
    @pytest.fixture
    async def remote(self, tmp_path: Path) -> AsyncIterator[RemoteStore]:
        class Plain(FileStore):
            history_page = None  # type: ignore[assignment]

        backing = Plain(tmp_path / "plain", clock=ManualClock())
        await seed(backing)
        remote = await _connect(backing)
        try:
            yield remote
        finally:
            await remote.aclose()

    async def test_listings_answer_whole(
        self, remote: RemoteStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paging, "PAGE", 2)
        history = await remote.history(SCOPE)
        assert len(history) == 15
        assert await remote.list_documents(SCOPE)
        assert len(await remote.history(SCOPE, limit=4)) == 4

    async def test_a_page_and_a_cursor_are_refused_by_name(
        self, remote: RemoteStore
    ) -> None:
        with pytest.raises(ConfigurationError, match="does not implement Pageable"):
            await remote.history_page(SCOPE, limit=2)
        with pytest.raises(ConfigurationError, match="Plain does not implement"):
            await remote._call(  # noqa: SLF001 — the raw route, a cursor in hand
                "memory/history", {"scope": SCOPE, "cursor": "abc"}
            )
