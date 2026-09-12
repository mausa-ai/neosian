"""IN-4: a token's allowance — what it reaches, not only who it is (§18.4).

Before NQ2 one bearer token reached every scope, every conversation and
the export routes; the actor it carried was an audit stamp and nothing
more. An allowance is an allowlist written by an operator: two literal
prefixes, neither interpreted by the library, and everything it does not
name is refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.server.app import build_app
from neosian._foundation.server.tokens import Client, parse_clients
from neosian._foundation.shared.exceptions import ConfigurationError

from .conftest import BASE_URL

# alice is fenced both ways; reader names scopes only; ops is today's token.
_TABLE = (
    "client:alice@user:alice+alice-=tok-alice,"
    "client:reader@user:alice=tok-reader,"
    "client:ops=tok-ops"
)


class TestTheGrammar:
    def test_an_allowance_rides_the_actor_side(self) -> None:
        clients = parse_clients(_TABLE)
        assert clients["tok-alice"] == Client(
            "client:alice", scopes="user:alice", conversations="alice-"
        )
        assert clients["tok-reader"] == Client("client:reader", scopes="user:alice")
        assert clients["tok-ops"] == Client("client:ops")

    def test_a_token_may_still_hold_any_character_but_a_comma(self) -> None:
        # The allowance is left of the '=', so '@' and '+' stay legal in a
        # token — the reason it is written on that side at all.
        clients = parse_clients("client:a@user:a=p@ss+w0rd=x")
        assert clients == {"p@ss+w0rd=x": Client("client:a", scopes="user:a")}

    @pytest.mark.parametrize(
        "value",
        [
            "client:a@=tok",  # an empty scope prefix
            "client:a+=tok",  # an empty id prefix
            "client:a@user:a+=tok",  # one of each, the second empty
        ],
    )
    def test_an_empty_prefix_refuses(self, value: str) -> None:
        with pytest.raises(ConfigurationError, match="prefix is empty"):
            parse_clients(value)

    def test_an_allowance_is_an_allowlist_not_a_filter(self) -> None:
        reader = parse_clients(_TABLE)["tok-reader"]
        # Naming scopes and not conversations reaches no conversation at
        # all — the half-boundary the ruling refused to ship.
        assert reader.may_reach_scope("user:alice/proj:erp") is True
        assert reader.may_reach_scope("user:bob") is False
        assert reader.may_reach_conversation("alice-1") is False
        assert reader.may_reach_conversation("anything") is False


@pytest.fixture
async def http(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = await build_app(FileStore(tmp_path / "state"), token=_TABLE)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield client


_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "allowance-test", "version": "0"},
    },
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post(
    http: httpx.AsyncClient,
    route: str,
    token: str,
    payload: Mapping[str, object],
) -> httpx.Response:
    return await http.post(f"/v1/{route}", headers=_bearer(token), json=payload)


class TestTheGate:
    async def test_a_fenced_token_reaches_its_own_scope(
        self, http: httpx.AsyncClient
    ) -> None:
        written = await _post(
            http,
            "memory/write",
            "tok-alice",
            {"scope": "user:alice/proj:erp", "path": "notes", "content": "x"},
        )
        assert written.status_code == 200
        read = await _post(
            http,
            "memory/read",
            "tok-alice",
            {"scope": "user:alice/proj:erp", "path": "notes"},
        )
        assert read.json()["document"]["content"] == "x"

    async def test_a_fenced_token_is_refused_another_scope(
        self, http: httpx.AsyncClient
    ) -> None:
        for route, payload in (
            ("memory/read", {"scope": "user:bob", "path": "notes"}),
            ("memory/write", {"scope": "user:bob", "path": "n", "content": "x"}),
            ("memory/redact", {"scope": "user:bob"}),
            ("memory/history", {"scope": "user:bob"}),
        ):
            response = await _post(http, route, "tok-alice", payload)
            assert response.status_code == 403, route
            error = response.json()["error"]
            assert error["code"] == "forbidden"
            assert "user:bob" in error["message"]

    async def test_a_prefix_is_literal_text_never_a_scope_segment(
        self, http: httpx.AsyncClient
    ) -> None:
        # `user:alice2` starts with `user:alice`: the library does not
        # interpret the grammar here, and §18.4 says so. An operator who
        # wants the boundary writes `user:alice/`.
        response = await _post(
            http, "memory/read", "tok-alice", {"scope": "user:alice2", "path": "n"}
        )
        assert response.status_code == 200

    async def test_a_fenced_token_reaches_only_its_conversations(
        self, http: httpx.AsyncClient
    ) -> None:
        mine = await _post(
            http,
            "conversation/append_turn",
            "tok-alice",
            {
                "conversation_id": "alice-s1",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert mine.status_code == 200
        theirs = await _post(
            http, "conversation/read_turns", "tok-alice", {"conversation_id": "bob-s1"}
        )
        assert theirs.status_code == 403
        assert "bob-s1" in theirs.json()["error"]["message"]

    async def test_naming_scopes_only_denies_every_conversation(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await _post(
            http, "conversation/read_turns", "tok-reader", {"conversation_id": "any"}
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "route",
        ["store/scopes", "store/conversations"],
    )
    async def test_the_export_routes_refuse_a_constrained_token(
        self, http: httpx.AsyncClient, route: str
    ) -> None:
        # Whole-store by construction, and restores are verbatim under the
        # archive's own actors (#166) — the trust an allowance withdraws.
        refused = await _post(http, route, "tok-alice", {})
        assert refused.status_code == 403
        assert "the whole store" in refused.json()["error"]["message"]
        assert (await _post(http, route, "tok-ops", {})).status_code == 200

    async def test_an_unconstrained_token_is_byte_identical_to_before(
        self, http: httpx.AsyncClient
    ) -> None:
        for route, payload in (
            ("memory/write", {"scope": "user:bob", "path": "n", "content": "x"}),
            ("memory/list_documents", {"scope": "user:zed"}),
            ("conversation/read_turns", {"conversation_id": "whatever"}),
            ("store/scopes", {}),
        ):
            response = await _post(http, route, "tok-ops", payload)
            assert response.status_code == 200, route


class TestTheMcpSurface:
    """`/mcp` serves the operator's mounts, so a prefix has nothing to
    match: a constrained token is refused it outright."""

    @asynccontextmanager
    async def _client(self, root: Path) -> AsyncIterator[httpx.AsyncClient]:
        # The session manager's task group must be entered in the test's
        # own task — test_mcp_surface's idiom, for the same reason.
        app = await build_app(
            FileStore(root),
            token=_TABLE,
            mounts=[Mount(scope="user:alice/proj:erp", mount_path="project")],
            actor="serve:test",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE_URL
            ) as client,
        ):
            yield client

    async def test_a_constrained_token_is_refused(self, tmp_path: Path) -> None:
        async with self._client(tmp_path / "mem") as client:
            response = await client.post(
                "/mcp", headers=_bearer("tok-alice"), json=_INITIALIZE
            )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
        assert "/mcp" in response.json()["error"]["message"]

    async def test_an_unconstrained_token_reaches_it(self, tmp_path: Path) -> None:
        async with self._client(tmp_path / "mem") as client:
            response = await client.post(
                "/mcp",
                headers=_bearer("tok-ops") | _MCP_HEADERS,
                json=_INITIALIZE,
            )
        assert response.status_code == 200
