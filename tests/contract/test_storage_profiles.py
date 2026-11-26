"""The storage conformance runner's profiles, run as a service author runs
them: the external HTTP mode against a server this runner did not start,
with no binary of its own.

The records profile is the full profile plus a required `turn_records`
version 1 (ruling D5). On a service that does not declare it the profile
fails before any case runs; it never passes by skipping. The full profile
on the same service passes and reports the records cases as skipped. A
service that does not declare the capability owes the five routes nothing
(ruling R30): one that answers them `404`, as a wire 6 service built before
they existed does, passes the full profile.
"""

from __future__ import annotations

import functools
import http.client
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tests.contract.support import ROOT, Binary, Server, serve

STORAGE = "tests/contract/storage/"
CAPABILITIES = "test_capabilities.py::"
IMPORTS = CAPABILITIES + "test_the_contract_tier_did_not_import_the_python_product"
ORDER = CAPABILITIES + "test_authentication_precedes_body_decoding"
UNDECLARED = CAPABILITIES + "test_undeclared_capabilities_refuse_their_operations"
SWEEP = "test_records_search.py::test_a_sweep_preview_removes_nothing"
CASES = [STORAGE + "test_capabilities.py", STORAGE + SWEEP]
OUTCOME = re.compile(
    r"^tests/contract/storage/(\S+) (PASSED|FAILED|ERROR|SKIPPED)", re.M
)
WITHOUT_RECORDS = {
    IMPORTS: "PASSED",
    ORDER: "PASSED",
    UNDECLARED: "PASSED",
    SWEEP: "SKIPPED",
}
RECORD_ROUTES = (
    "/v1/conversation/write_records",
    "/v1/conversation/read_records",
    "/v1/conversation/search_records",
    "/v1/store/restore_records",
    "/v1/store/sweep_records",
)
# Each case starts nested runs of four cases; generous for a loaded machine.
pytestmark = pytest.mark.timeout(900)


def conformance(server: Server, profile: str) -> tuple[int, dict[str, str], str]:
    """One external run of two files of the suite: its exit code, each
    case's outcome and its output. `NEOSIAN_BINARY` names a file that does
    not exist: the HTTP mode must never look for one."""
    env = {key: value for key, value in os.environ.items() if key != "NEOSIAN_BINARY"}
    env.update(
        NEOSIAN_TEST_SERVER_URL=server.url,
        NEOSIAN_TEST_SERVER_TOKEN=server.token,
        NEOSIAN_BINARY=str(ROOT / "target" / "no-such-binary"),
    )
    env.pop("NEOSIAN_TEST_SERVER_PEER_URL", None)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--confcutdir=tests/contract",
            *CASES,
            "-m",
            "contract",
            "-v",
            "-rs",
            "--timeout=180",
            "-p",
            "no:cacheprovider",
            "--storage-backend=http",
            f"--storage-profile={profile}",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=400,
        check=False,
    )
    output = done.stdout + done.stderr
    return done.returncode, dict(OUTCOME.findall(done.stdout)), output


class Older(BaseHTTPRequestHandler):
    """A service from before turn records: every request is forwarded to
    the server behind it, except the five routes, which it never had."""

    target = 0

    def do_GET(self) -> None:
        self.exchange()

    def do_POST(self) -> None:
        self.exchange()

    def log_message(self, *_args: object) -> None:
        pass

    def exchange(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        extra: list[tuple[str, str]] = []
        if self.path in RECORD_ROUTES:
            status, kind, answer = 404, "text/plain; charset=utf-8", b"Not Found"
        else:
            link = http.client.HTTPConnection("127.0.0.1", self.target, timeout=30)
            try:
                headers = {
                    name: value
                    for name, value in self.headers.items()
                    if name.lower() in ("authorization", "content-type")
                }
                link.request(self.command, self.path, body=body, headers=headers)
                reply = link.getresponse()
                status, answer = reply.status, reply.read()
                kind = reply.getheader("Content-Type", "text/plain")
                challenge = reply.getheader("WWW-Authenticate")
                if challenge:
                    extra.append(("WWW-Authenticate", challenge))
            finally:
                link.close()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(answer)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(answer)


@contextmanager
def older(server: Server) -> Iterator[Server]:
    port = urlsplit(server.url).port
    assert port is not None
    handler = type("OlderService", (Older,), {"target": port})
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(
        target=functools.partial(proxy.serve_forever, poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield Server(f"http://127.0.0.1:{proxy.server_port}", server.token)
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(5)


def test_the_records_profile_requires_the_capability(server: Server) -> None:
    code, outcomes, output = conformance(server, "records")
    assert code == 1, output
    assert "records profile requires turn_records version 1" in output
    # Every case that asks for the service errors; the one that does not
    # (the import check) is the only one to pass. Nothing is skipped.
    assert outcomes == {
        IMPORTS: "PASSED",
        ORDER: "ERROR",
        UNDECLARED: "ERROR",
        SWEEP: "ERROR",
    }, output
    # The same service passes the full profile; the records case is skipped
    # by name, not counted as verified.
    code, outcomes, output = conformance(server, "full")
    assert code == 0, output
    assert outcomes == WITHOUT_RECORDS, output
    assert "service does not declare optional turn records version 1" in output


def test_a_service_without_the_capability_owes_the_routes_nothing(
    server: Server,
) -> None:
    with older(server) as service:
        missing = service.request("/v1/store/sweep_records", {})
        assert (missing.status, missing.body) == (404, "Not Found")
        code, outcomes, output = conformance(service, "full")
    assert code == 0, output
    assert outcomes == WITHOUT_RECORDS, output


def test_the_records_profile_passes_on_a_store_that_keeps_records(
    binary: Binary,
) -> None:
    (binary.home / ".records").mkdir(parents=True)
    (binary.home / ".records" / "format").write_text(
        '{"neosian_records":1}\n', encoding="utf-8"
    )
    with serve(binary) as records:
        for profile in ("records", "full"):
            code, outcomes, output = conformance(records, profile)
            assert code == 0, output
            assert len(outcomes) == 4, output
            assert set(outcomes.values()) == {"PASSED"}, output
