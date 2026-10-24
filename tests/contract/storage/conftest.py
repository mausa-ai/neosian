"""Disposable local backends or explicitly selected external HTTP test servers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack
from uuid import uuid4

import pytest

from tests.contract.schema import OPENAPI, validate_schema
from tests.contract.storage.support import Storage, refused
from tests.contract.support import ROOT, Binary, Server, executable, serve


@pytest.fixture(scope="session")
def endpoints(
    pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Server, Server]]:
    backend = pytestconfig.getoption("storage_backend")
    if backend == "http":
        url = os.environ.get("NEOSIAN_TEST_SERVER_URL", "")
        token = os.environ.get("NEOSIAN_TEST_SERVER_TOKEN", "")
        assert (
            url and token
        ), "set NEOSIAN_TEST_SERVER_URL and NEOSIAN_TEST_SERVER_TOKEN"
        primary = Server(url.rstrip("/"), token)
        peer_url = os.environ.get("NEOSIAN_TEST_SERVER_PEER_URL", url)
        yield primary, Server(peer_url.rstrip("/"), token)
        return
    binary = Binary(executable(), tmp_path_factory.mktemp("storage-contract"))
    with ExitStack() as stack:
        dsn = schema = None
        if backend == "postgres":
            import psycopg
            from psycopg import sql

            dsn = os.environ.get("NEOSIAN_TEST_POSTGRES_DSN", "")
            assert dsn, "set NEOSIAN_TEST_POSTGRES_DSN for --storage-backend=postgres"
            schema = "neosian_contract_" + uuid4().hex
            connection = stack.enter_context(psycopg.connect(dsn, autocommit=True))
            # The fixture owns this new schema. The server never applies DDL.
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
            )
            stack.callback(
                connection.execute,
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)),
            )
            ddl = (ROOT / "neosian/assets/sql/postgres.sql").read_text(encoding="utf-8")
            ddl = ddl.replace("{{schema}}", '"' + schema + '"').replace(
                "{{version}}", "4"
            )
            connection.execute(ddl)
        primary = stack.enter_context(serve(binary, postgres_dsn=dsn, schema=schema))
        peer = stack.enter_context(serve(binary, postgres_dsn=dsn, schema=schema))
        yield primary, peer


@pytest.fixture(scope="session")
def storage_profile(
    endpoints: tuple[Server, Server], pytestconfig: pytest.Config
) -> tuple[dict[str, object], bool]:
    server, peer = endpoints
    reply = server.request("/v1/capabilities", method="GET")
    assert reply.status == 200
    schema = OPENAPI["paths"]["/v1/capabilities"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    validate_schema(schema, reply.body)
    capabilities = reply.body
    peer_reply = peer.request("/v1/capabilities", method="GET")
    assert peer_reply.status == 200
    validate_schema(schema, peer_reply.body)
    for key in (
        "wire_version",
        "client",
        "supports_optimistic_concurrency",
        "pageable",
        "erasable",
    ):
        assert peer_reply.body[key] == capabilities[key], f"peer differs on {key}"
    portability = server.request("/v1/store/scopes", {})
    portable = portability.status == 200
    if not portable:
        refused(portability, "agent_configuration_error")
    if pytestconfig.getoption("storage_profile") == "full":
        for capability in ("supports_optimistic_concurrency", "pageable", "erasable"):
            assert (
                capabilities[capability] is True
            ), f"full profile requires {capability}"
        assert portable, "full profile requires whole-store portability"
    return capabilities, portable


@pytest.fixture
def storage(
    endpoints: tuple[Server, Server], storage_profile: tuple[dict[str, object], bool]
) -> Storage:
    capabilities, portable = storage_profile
    return Storage(*endpoints, "contract-" + uuid4().hex, capabilities, portable)
