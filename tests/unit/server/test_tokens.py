"""Per-client tokens and server-asserted actors (NL, DESIGN §20)."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from neosian import RemoteStore
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.server.app import build_app
from neosian._foundation.server.settings import SERVE_TOKEN_ENV, parse_args
from neosian._foundation.server.tokens import DEFAULT_CLIENT, parse_clients, stamp
from neosian._foundation.shared.exceptions import ConfigurationError

from .conftest import BASE_URL

_TABLE = "claude-code:laptop=abc,app:kit=def"


class TestParseClients:
    def test_a_bare_token_is_the_default_client(self) -> None:
        assert parse_clients("secret") == {"secret": DEFAULT_CLIENT}

    def test_a_table_maps_tokens_to_actors(self) -> None:
        assert parse_clients(_TABLE) == {"abc": "claude-code:laptop", "def": "app:kit"}

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "claude-code:laptop=",  # empty token
            "=abc",  # empty actor
            "laptop=abc",  # an ungrammatical actor
            "a:1=abc,a:1=xyz",  # a repeated actor
            "a:1=abc,b:2=abc",  # a repeated token
            "a:1=abc,,b:2=def",  # an empty entry
        ],
    )
    def test_malformed_tables_refuse(self, value: str) -> None:
        with pytest.raises(ConfigurationError):
            parse_clients(value)

    def test_refusals_never_echo_a_token(self) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            parse_clients("laptop=hunter2")
        assert "hunter2" not in str(exc_info.value)

    def test_stamp(self) -> None:
        assert stamp("claude-code:laptop", None) == "claude-code:laptop"
        assert stamp("claude-code:laptop", "conv:x#3") == "claude-code:laptop/conv:x#3"


class TestGrammarTier:
    def test_a_malformed_table_exits_two(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--root", str(tmp_path)], {SERVE_TOKEN_ENV: "laptop=abc"})
        assert exc_info.value.code == 2

    def test_a_table_parses(self, tmp_path: Path) -> None:
        settings = parse_args(["--root", str(tmp_path)], {SERVE_TOKEN_ENV: _TABLE})
        assert settings.token == _TABLE


@pytest.fixture
async def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    return await build_app(FileStore(tmp_path / "state"), token=_TABLE)


@pytest.fixture
async def http(app) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        yield client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTheGate:
    async def test_each_token_asserts_its_client(self, http: httpx.AsyncClient) -> None:
        for token, client in (("abc", "claude-code:laptop"), ("def", "app:kit")):
            response = await http.get("/v1/capabilities", headers=_bearer(token))
            assert response.status_code == 200
            assert response.json()["client"] == client

    async def test_an_unknown_token_is_401(self, http: httpx.AsyncClient) -> None:
        response = await http.get("/v1/capabilities", headers=_bearer("ghi"))
        assert response.status_code == 401

    async def test_a_bodyless_write_records_the_client(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.post(
            "/v1/memory/write",
            json={"scope": "user:me", "path": "a", "content": "x"},
            headers=_bearer("abc"),
        )
        assert response.json()["document"]["actor"] == "claude-code:laptop"

    async def test_a_body_actor_lands_under_the_client(
        self, http: httpx.AsyncClient
    ) -> None:
        """The client's word is subordinate: it can name a sub-identity
        (its conversation and turn) but never another client's prefix."""
        await http.post(
            "/v1/memory/write",
            json={"scope": "user:me", "path": "a", "content": "x", "actor": "conv:x#3"},
            headers=_bearer("abc"),
        )
        forged = await http.post(
            "/v1/memory/write",
            json={"scope": "user:me", "path": "b", "content": "y", "actor": "app:kit"},
            headers=_bearer("abc"),
        )
        assert forged.json()["document"]["actor"] == "claude-code:laptop/app:kit"
        history = await http.post(
            "/v1/memory/history", json={"scope": "user:me"}, headers=_bearer("def")
        )
        assert {row["actor"] for row in history.json()["versions"]} == {
            "claude-code:laptop/conv:x#3",
            "claude-code:laptop/app:kit",
        }

    async def test_turns_and_redactions_are_stamped_too(
        self, http: httpx.AsyncClient
    ) -> None:
        turn = await http.post(
            "/v1/conversation/append_turn",
            json={
                "conversation_id": "c1",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=_bearer("def"),
        )
        assert turn.json()["turn"]["actor"] == "app:kit"
        await http.post(
            "/v1/memory/write",
            json={"scope": "user:me", "path": "a", "content": "x"},
            headers=_bearer("abc"),
        )
        await http.post(
            "/v1/memory/redact", json={"scope": "user:me"}, headers=_bearer("def")
        )
        acts = await http.post(
            "/v1/memory/redactions", json={"scope": "user:me"}, headers=_bearer("abc")
        )
        assert acts.json()["redactions"][0]["actor"] == "app:kit"


class TestRemoteStoreLearnsItsClient:
    async def test_connect_exposes_the_asserted_client(self, app) -> None:  # type: ignore[no-untyped-def]
        store = await RemoteStore.connect(
            BASE_URL, token="def", transport=httpx.ASGITransport(app=app)
        )
        try:
            assert store.client == "app:kit"
            turn = await store.append_turn(
                "c1", [Message(role=Role.USER, content="hi")]
            )
            assert turn.actor == "app:kit"
        finally:
            await store.aclose()

    def test_the_plain_constructor_knows_no_client(self) -> None:
        assert RemoteStore(BASE_URL, token="x").client is None


class TestServeRefusesAUrl:
    def test_a_daemon_over_a_daemon_is_grammar(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            parse_args(
                ["--url", "http://h:1"],
                {SERVE_TOKEN_ENV: "t", "NEOSIAN_CLIENT_TOKEN": "c"},
            )
        assert exc_info.value.code == 2
