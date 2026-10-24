"""A process and raw HTTP client, with no product codec or store imports."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "neosian" / "assets" / "contracts"
FIXTURES = Path(__file__).parent / "fixtures" / "v1"
ADMIN = "contract-admin"
LIMITED = "contract-limited"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def package_version() -> str:
    with (ROOT / "Cargo.toml").open("rb") as handle:
        return str(tomllib.load(handle)["workspace"]["package"]["version"])


@dataclass(frozen=True)
class Binary:
    executable: Path
    directory: Path

    @property
    def home(self) -> Path:
        return self.directory / "home"

    def environment(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("NEOSIAN_") and not key.endswith("_API_KEY")
        }
        # Isolate all client configuration and store selection from the operator.
        for key in (
            "CLAUDE_CONFIG_DIR",
            "CODEX_HOME",
            "OPENCODE_CONFIG_DIR",
            "PI_CODING_AGENT_DIR",
            "XDG_CONFIG_HOME",
            "APPDATA",
            "LOCALAPPDATA",
        ):
            env.pop(key, None)
        env.update(
            HOME=str(self.directory),
            USERPROFILE=str(self.directory),
            NEOSIAN_HOME=str(self.home),
            LOGNAME="contract",
            NO_COLOR="1",
        )
        return env

    def run(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.executable), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.environment(),
            cwd=self.directory,
            timeout=30,
            check=False,
        )

    def json(self, *args: str) -> Any:
        result = self.run(*args, "--json")
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)


@dataclass(frozen=True)
class Reply:
    status: int
    body: Any
    headers: dict[str, str]


@dataclass(frozen=True)
class Server:
    url: str

    def request(
        self,
        path: str,
        body: Any = None,
        *,
        method: str = "POST",
        token: str | None = ADMIN,
        raw: bytes | None = None,
    ) -> Reply:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        data: bytes | None = (
            raw if raw is not None else json.dumps(body).encode("utf-8")
        )
        if method == "GET":
            data = None
        request = Request(self.url + path, data=data, headers=headers, method=method)
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            content = response.read().decode("utf-8")
            parsed = (
                json.loads(content)
                if response.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
                else content
            )
            return Reply(
                response.status,
                parsed,
                {name.lower(): value for name, value in response.headers.items()},
            )

    def post(self, route: str, **body: Any) -> Any:
        from tests.contract.schema import validate_request, validate_response

        validate_request(route, body)
        reply = self.request("/v1/" + route, body)
        assert reply.status == 200, reply
        validate_response(route, reply.body)
        return reply.body


@contextmanager
def serve(binary: Binary) -> Iterator[Server]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = binary.environment()
    env["NEOSIAN_SERVE_TOKEN"] = (
        f"client:contract={ADMIN},client:limited@user:allowed+allowed-={LIMITED}"
    )
    process = subprocess.Popen(
        [
            str(binary.executable),
            "serve",
            "--root",
            str(binary.home),
            "--port",
            str(port),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=binary.directory,
    )
    server = Server(f"http://127.0.0.1:{port}")
    try:
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode("utf-8"))
            try:
                if server.request("/health", method="GET", token=None).status == 200:
                    break
            except (URLError, TimeoutError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("contract server did not become healthy")
            time.sleep(0.02)
        yield server
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)
