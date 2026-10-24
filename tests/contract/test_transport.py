"""Loopback transport probes, including verified HTTPS. The committed key
belongs only to this test certificate (expires 2036), never a service."""

from __future__ import annotations

import json
import ssl
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.contract.support import Binary

TOKEN = "transport-test"
CERTS = Path(__file__).with_name("certs")
Reply = Callable[[str, dict[str, Any]], tuple[int, Any]]


@contextmanager
def responder(
    reply: Reply, *, tls: bool = False, certificate: str = "server.pem"
) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.answer({})

        def do_POST(self) -> None:
            self.answer(
                json.loads(self.rfile.read(int(self.headers["content-length"])))
            )

        def answer(self, body: dict[str, Any]) -> None:
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            requests.append(self.path)
            status, value = reply(self.path, body)
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(CERTS / certificate, CERTS / "server-key.pem")
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def invoke(binary: Binary, url: str, ca: str = "") -> subprocess.CompletedProcess[str]:
    env = {**binary.environment(), "NEOSIAN_CLIENT_TOKEN": TOKEN, "SSL_CERT_FILE": ca}
    return subprocess.run(
        [
            str(binary.executable),
            "memory",
            "view",
            "/",
            "--scope",
            "user:test",
            "--url",
            url,
        ],
        env=env,
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("version", [None, 999])
def test_foreign_wire_refused_before_storage(
    binary: Binary, version: int | None
) -> None:
    with responder(lambda _path, _body: (200, {"wire_version": version})) as (
        url,
        requests,
    ):
        result = invoke(binary, url)
        assert result.returncode == 1 and "wire version skew" in result.stderr
        assert requests == ["/v1/capabilities"]
        assert not binary.home.exists()


def test_https_requires_trust(binary: Binary) -> None:
    def answer(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "client": "client:tls", "pageable": True}
        assert path == "/v1/memory/list_documents"
        return 200, {"entries": [], "next_cursor": None}

    with responder(answer, tls=True) as (url, requests):
        refused = invoke(binary, url)
        assert refused.returncode == 1 and requests == []
        accepted = invoke(binary, url, str(CERTS / "ca.pem"))
        assert accepted.returncode == 0, accepted.stderr
        assert requests == ["/v1/capabilities", "/v1/memory/list_documents"]
        assert not binary.home.exists()


@pytest.mark.parametrize(
    "option",
    [
        "sslmode=verify-full",
        "sslmode=verify-ca",
        "service=legacy",
        "sslrootcert=/private/ca",
        "sslcert=/private/client.pem",
        "password='secret-token' unknown_option=1",
    ],
)
def test_libpq_only_dsn_options_fail_value_blind(binary: Binary, option: str) -> None:
    env = {
        **binary.environment(),
        "NEOSIAN_POSTGRES_DSN": "host=127.0.0.1 user=reader dbname=test " + option,
    }
    result = subprocess.run(
        [str(binary.executable), "memory", "view", "/", "--scope", "user:test"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert "invalid Postgres connection string" in result.stderr
    assert "secret-token" not in result.stderr and "/private" not in result.stderr
    assert not binary.home.exists()


def test_https_checks_hostname_even_with_a_trusted_certificate(binary: Binary) -> None:
    def answer(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "pageable": True}
        return 200, {"entries": [], "next_cursor": None}

    certificate = "localhost-only.pem"
    with responder(answer, tls=True, certificate=certificate) as (url, requests):
        trusted = str(CERTS / certificate)
        accepted = invoke(binary, url.replace("127.0.0.1", "localhost"), trusted)
        assert accepted.returncode == 0, accepted.stderr
        requests.clear()
        refused = invoke(binary, url, trusted)
        assert refused.returncode == 1 and requests == []
        assert not binary.home.exists()
