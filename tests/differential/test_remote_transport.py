"""Loopback transport probes, including verified HTTPS. The committed key
belongs only to this test certificate (expires 2036), never a service."""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from neosian import RemoteStore
from neosian._foundation.server.wire import WIRE_VERSION
from neosian._foundation.shared.exceptions import ConfigurationError
from tests.differential.harness import TOKEN, Case, Workspace, run_case, shape_of_output
from tests.differential.remote_cases import remote_case
from tests.unit.record.payloads import SESSION, session_start

CERTS = Path(__file__).with_name("certs")
Reply = Callable[[str, dict[str, Any]], tuple[int, Any]]


@contextmanager
def responder(reply: Reply, *, tls: bool = False) -> Iterator[tuple[str, list[str]]]:
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
        context.load_cert_chain(CERTS / "server.pem", CERTS / "server-key.pem")
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def blank(tmp_path: Path) -> Workspace:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    return Workspace(
        tmp_path / "home", project, tmp_path / "out", "user:test", "proj:test", SESSION
    )


def startup() -> Case:
    return Case(
        "remote start",
        ("record", "--root", "{home}", "--scope", "user:test", "--json"),
        payload=session_start(),
    )


@pytest.mark.parametrize("version", [None, 999])
async def test_both_clients_refuse_foreign_wire_before_store_calls(
    version: int | None, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    body = {} if version is None else {"wire_version": version}
    with responder(lambda _path, _body: (200, body)) as (url, requests):
        with pytest.raises(ConfigurationError, match="wire version skew"):
            await RemoteStore.connect(url, token=TOKEN)
        for door in (python_door, rust_door):
            for verb in ("record", "mcp"):
                space = blank(tmp_path / door.parent.name / verb)
                case = remote_case(startup(), url)
                if verb == "mcp":
                    case = replace(
                        case,
                        argv=("mcp", "--url", url, "--scope", "user:test"),
                        payload=None,
                    )
                outcome = run_case(door, case, space)
                assert outcome.code == 1
                assert b"wire version skew" in outcome.stderr
                assert not space.home.exists()
        assert requests == ["/v1/capabilities"] * 5


def _empty(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
    if path == "/v1/capabilities":
        return 200, {
            "wire_version": WIRE_VERSION,
            "client": "client:tls",
            "pageable": True,
        }
    if path == "/v1/memory/list_documents":
        return 200, {"entries": [], "next_cursor": None}
    if path == "/v1/memory/read":
        return 200, {"document": None}
    raise AssertionError(path)


def test_https_requires_trust_and_succeeds_with_a_ca_file(
    python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    with responder(_empty, tls=True) as (url, requests):
        observed = []
        for door in (python_door, rust_door):
            space = blank(tmp_path / door.parent.name)
            case = remote_case(startup(), url)
            untrusted = replace(case, env={**case.env, "SSL_CERT_FILE": ""})
            refused = run_case(door, untrusted, space)
            assert refused.code == 1
            assert not requests
        for door in (python_door, rust_door):
            space = blank(tmp_path / (door.parent.name + "-trusted"))
            case = remote_case(startup(), url)
            trusted = replace(
                case, env={**case.env, "SSL_CERT_FILE": str(CERTS / "ca.pem")}
            )
            accepted = run_case(door, trusted, space)
            assert accepted.code == 0, accepted.stderr
            observed.append(
                shape_of_output(trusted, accepted, space, keep=frozenset({SESSION}))
            )
        assert observed[0] == observed[1]
        assert not (tmp_path / "release-trusted" / "home").exists()


@pytest.mark.parametrize("verb", ["record", "mcp"])
@pytest.mark.parametrize("kind", ["missing", "empty", "scheme", "root", "dsn"])
def test_remote_grammar_builds_nothing(
    verb: str, kind: str, python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed = []
    for door in (python_door, rust_door):
        space = blank(tmp_path / door.parent.name)
        url = "ftp://invalid" if kind == "scheme" else "http://127.0.0.1:1"
        argv: tuple[str, ...] = (verb, "--url", url, "--scope", "user:test")
        env = (
            {}
            if kind == "missing"
            else {"NEOSIAN_CLIENT_TOKEN": "" if kind == "empty" else TOKEN}
        )
        if kind == "root":
            argv += ("--root", "{home}")
        if kind == "dsn":
            env["NEOSIAN_POSTGRES_DSN"] = "postgresql://unused"
        case = Case("invalid remote", argv, payload=session_start(), env=env)
        outcome = run_case(door, case, space)
        assert outcome.code == 2
        assert not space.home.exists()
        observed.append(shape_of_output(case, outcome, space, keep=frozenset()))
    assert observed[0] == observed[1]


def test_mailbox_updates_require_the_servers_concurrency_capability(
    python_door: Path, rust_door: Path, tmp_path: Path
) -> None:
    observed = []
    capabilities = {
        "wire_version": WIRE_VERSION,
        "supports_optimistic_concurrency": False,
        "client": "client:limited",
    }
    with responder(lambda _path, _body: (200, capabilities)) as (url, requests):
        for door in (python_door, rust_door):
            space = blank(tmp_path / door.parent.name)
            case = remote_case(startup(), url)
            case = replace(
                case,
                payload={
                    "session_id": SESSION,
                    "hook_event_name": "MailboxDelivery",
                    "scope": "user:test",
                    "message_id": "00000000-0000-4000-8000-000000000000",
                    "occurrence": 1,
                    "token": "unused",
                    "accepted": True,
                },
            )
            outcome = run_case(door, case, space)
            assert outcome.code == 1
            assert (
                b"mailbox updates require cross-worker optimistic concurrency"
                in outcome.stderr
            )
            observed.append(shape_of_output(case, outcome, space, keep=frozenset()))
        assert requests == ["/v1/capabilities"] * 2
        assert observed[0] == observed[1]
