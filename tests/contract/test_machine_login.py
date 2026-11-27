"""First-party device and environment logins against an isolated HTTPS host."""

from __future__ import annotations

import json
import os
import ssl
import stat
import subprocess
import threading
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.contract.profile_support import PREFIX, PROFILE, WORKSPACE, run
from tests.contract.support import Binary
from tests.contract.test_setup_check import setup
from tests.contract.test_transport import CERTS

SECRET = "contract-issued-device-secret"
CODE = "private-device-code"
NOTICE = "This host saves prompts and assistant replies for shared capture."
TRUST = {"SSL_CERT_FILE": str(CERTS / "ca.pem")}


def profile() -> dict[str, Any]:
    result = tomllib.loads(PROFILE)
    result["context"] = {"max_chars": 512}
    result["skills"] = ["builtin:upkeep"]
    result["history"] = {
        "source": "knowledge",
        "scope": "workspace:portable",
        "prefix": PREFIX,
    }
    result["capture"] = {"mode": "shared", "fields": ["prompt", "assistant"]}
    return result


@dataclass
class Host:
    url: str = ""
    account: str = "account-one"
    names: tuple[str, ...] = ("personal",)
    notice: str = NOTICE
    etag: str = '"1"'
    state_path: str = "/state"
    tokens: tuple[str, ...] = (SECRET,)
    issued_token: str = SECRET
    revocation_succeeds: bool = True
    device: dict[str, str] | None = None
    locked: bool = False
    refresh_seconds: int = 3600
    label: str = "Portable knowledge"
    create: bool = False
    endpoint: str | None = None
    deny: bool = False
    hold: tuple[threading.Event, threading.Event] | None = None
    polls: int = 0
    calls: list[tuple[str, str | None, str | None, dict[str, Any]]] = field(
        default_factory=list
    )
    # Every request as (method, path, Neosian-Capabilities, If-None-Match).
    seen: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    # The capability value whose document `etag` names: a 304 needs both.
    variant: str = "turn_records=1"
    failures: int = 0

    def reply(
        self,
        path: str,
        token: str | None,
        tag: str | None,
        data: dict[str, Any],
        capabilities: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((path, token, tag, data))
        if path == "/host":
            assert token is None
            return 200, {
                "host_format": 1,
                "name": "Test host",
                "device_authorization_endpoint": self.url + "/device",
                "token_endpoint": self.url + "/token",
                "machine_endpoint": self.endpoint or self.url + "/machine",
                "workspaces_endpoint": (
                    self.url + "/workspaces" if self.create else None
                ),
                "revocation_endpoint": self.url + "/logout",
                "scopes": ["state:read", "state:write"],
            }
        if path == "/device":
            assert token is None and data["scopes"] == ["state:read", "state:write"]
            return 201, {
                "device_code": CODE,
                "user_code": "BCDF-GHJK",
                "verification_uri": self.url + "/approve",
                "verification_uri_complete": self.url + "/approve?user_code=BCDF-GHJK",
                "expires_in": 30,
                "interval": 1,
            }
        if path == "/token":
            assert token is None and data == {"device_code": CODE}
            self.polls += 1
            if self.deny:
                return 400, {"error": "access_denied"}
            if self.polls == 1:
                return 400, {"code": "authorization_pending"}
            if self.polls == 2:
                return 429, {"code": "slow_down", "details": {"interval": 1}}
            return 200, {
                "id": "device-id",
                "name": "fixture",
                "secret": self.issued_token,
                "expires_at": None,
            }
        if token not in {f"Bearer {value}" for value in self.tokens}:
            return 401, {}
        if path == "/logout":
            return (204 if self.revocation_succeeds else 503), {}
        if path == "/state/v1/capabilities":
            return 200, {
                "wire_version": 6,
                "authorization": {"version": 1, "mode": "scoped"},
            }
        if path == "/state/v1/memory/list_documents":
            return 200, {"entries": [], "next_cursor": None}
        if path == "/workspaces" and self.create:
            self.names = ("personal", "created")
            return 201, {"profile": "created"}
        if path == "/machine":
            if self.hold:
                entered, resume = self.hold
                entered.set()
                assert resume.wait(10)
            if self.failures:
                self.failures -= 1
                return 503, {}
            if tag == self.etag and capabilities == self.variant:
                return 304, {}
            return 200, {
                "machine_format": 1,
                "account": {"id": self.account, "email": "user@example.test"},
                "state_url": self.url + self.state_path,
                "default_profile": "personal",
                "profiles": {
                    name: {**profile(), "name": self.label} for name in self.names
                },
                "device": self.device,
                "capture_locked_off": self.locked,
                "notice": self.notice,
                "refresh_after_seconds": self.refresh_seconds,
            }
        return 404, {}


@contextmanager
def host_server() -> Iterator[Host]:
    host = Host()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.respond({})

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond(json.loads(raw) if raw else {})

        def respond(self, body: dict[str, Any]) -> None:
            stated = self.headers.get("Neosian-Capabilities")
            tag = self.headers.get("If-None-Match")
            host.seen.append((self.command, self.path, stated, tag))
            code, data = host.reply(
                self.path, self.headers.get("Authorization"), tag, body, stated
            )
            self.send_response(code)
            payload = b"" if code in (204, 304) else json.dumps(data).encode()
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("ETag", host.etag)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(CERTS / "server.pem", CERTS / "server-key.pem")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    host.url = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_device_login_refresh_logout(binary: Binary) -> None:
    with host_server() as host:
        result = run(
            binary,
            "login",
            "--host",
            host.url + "/host",
            "--workspace",
            "none",
            "--json",
            env=TRUST,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert NOTICE in result.stderr
        assert CODE not in result.stderr + result.stdout
        assert SECRET not in result.stderr + result.stdout
        start, end = map(json.loads, result.stdout.splitlines())
        assert start["user_code"] == "BCDF-GHJK" and end["event"] == "connected"
        assert host.polls == 3
        cred = binary.home / "login" / "credential.toml"
        if os.name == "nt":
            assert SECRET.encode() not in cred.read_bytes()
        else:
            assert stat.S_IMODE(cred.stat().st_mode) == 0o600
            assert stat.S_IMODE(cred.parent.stat().st_mode) == 0o700
        status = binary.run("status", "--json")
        assert json.loads(status.stdout)["login"]["connected"] is True
        assert json.loads(status.stdout)["login"]["device"] == {
            "id": "device-id",
            "name": "fixture",
        }
        assert json.loads(status.stdout)["profile"]["ready"] is True
        assert SECRET not in status.stdout
        viewed = run(binary, "memory", "view", "/project", env=TRUST)
        assert viewed.returncode == 0, (viewed.stdout, viewed.stderr)
        assert host.calls[-1][0] == "/state/v1/memory/list_documents"
        refreshed = run(binary, "login", "--refresh", "--json", env=TRUST)
        assert json.loads(refreshed.stdout)["updated"] is False
        assert host.calls[-1][2] == '"1"'
        host.names = ("personal", "assigned")
        host.etag = '"2"'
        edited = run(
            binary, "login", "--refresh", "--capture", "off", "--json", env=TRUST
        )
        assert edited.returncode == 0
        installed = binary.home / "profiles" / "assigned.toml"
        assert tomllib.loads(installed.read_text())["capture"]["mode"] == "off"
        host.names = ("personal",)
        host.etag = '"3"'
        assert run(binary, "login", "--refresh", env=TRUST).returncode == 0
        assert not installed.exists()
        assert (
            json.loads(binary.run("status", "--json").stdout)["profile"]["capture"]
            == "off"
        )
        pre = binary.run("setup", "--client", "codex", "--json")
        assert SECRET not in pre.stdout and "--profile" not in pre.stdout
        options = setup(binary, "pi")
        check = run(binary, "setup", *options, "--check", "--json", env={"PATH": ""})
        assert check.returncode == 0, check.stdout + check.stderr
        assert json.loads(check.stdout)["storage"]["profile_selection"] == "per_project"
        setup(binary, "pi", "--profile", "personal")
        pinned = run(binary, "setup", *options, "--check", "--json", env={"PATH": ""})
        assert pinned.returncode == 1 and "storage differs" in pinned.stdout
        logged_out = run(binary, "logout", "--json", env=TRUST)
        assert json.loads(logged_out.stdout)["revoked"] and not cred.exists()
        assert (
            json.loads(binary.run("status", "--json").stdout)["profile"]["ready"]
            is False
        )
        gone = run(binary, "logout", "--forget", "--json", env=TRUST)
        assert gone.returncode == 0 and "locally" in gone.stderr
        assert not (binary.home / "profiles" / "personal.toml").exists()


def test_managed_headless_and_account_isolation(binary: Binary) -> None:
    env = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
    with host_server() as host:
        args = (
            "login",
            "--host",
            host.url + "/host",
            "--non-interactive",
            "--workspace",
            "none",
            "--json",
        )
        result = run(binary, *args, env=env)
        assert result.returncode == 0, result.stdout
        assert NOTICE in result.stderr
        assert SECRET not in result.stdout + result.stderr
        for path in binary.home.rglob("*"):
            if path.is_file():
                assert SECRET.encode() not in path.read_bytes()
        assert (
            json.loads(run(binary, "status", "--json", env=env).stdout)["profile"][
                "workspace_id"
            ]
            == WORKSPACE
        )
        other = {**env, "NEOSIAN_LOGIN_TOKEN": "unvalidated-replacement"}
        assert not json.loads(run(binary, "status", "--json", env=other).stdout)[
            "profile"
        ]["ready"]
        host.account = "account-two"
        assert run(binary, *args, env=env).returncode == 1
        assert (
            json.loads(run(binary, "status", "--json", env=env).stdout)["login"][
                "account"
            ]["id"]
            == "account-one"
        )
        host.account = "account-one"
        host.notice = ""
        host.etag = '"4"'
        assert run(binary, "login", "--refresh", env=env).returncode == 0
        status = json.loads(run(binary, "status", "--json", env=env).stdout)
        assert status["profile"]["capture"] == "off" and status["profile"]["ready"]
        assert run(binary, "logout", "--forget", env=env).returncode == 0


def test_preserves_local_profile(binary: Binary) -> None:
    env = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
    assert binary.run("profile", "init", "personal", "--write").returncode == 0
    local = binary.home / "profiles" / "personal.toml"
    saved = local.read_bytes()
    with host_server() as host:
        result = run(
            binary, "login", "--host", host.url + "/host", "--non-interactive", env=env
        )
        assert result.returncode == 1 and local.read_bytes() == saved
        assert not (binary.home / "login" / "state.toml").exists()


def headless(
    binary: Binary, host: Host, token: str = SECRET, *args: str
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "login",
        "--non-interactive",
        "--json",
        "--host",
        host.url + "/host",
        *args,
        env={**TRUST, "NEOSIAN_LOGIN_TOKEN": token},
    )


def record(
    binary: Binary, token: str, event: str, **payload: str
) -> subprocess.CompletedProcess[str]:
    return run(
        binary,
        "record",
        "--agent",
        "codex",
        "--json",
        stdin=json.dumps(
            {"hook_event_name": event, "session_id": "managed-session", **payload}
        ),
        env={**TRUST, "NEOSIAN_LOGIN_TOKEN": token},
    )


def test_pending_capture_requires_verified_rotation(binary: Binary) -> None:
    new = "second-opaque-credential"
    with host_server() as host:
        host.tokens = (SECRET, new)
        host.device = {"id": "managed-device", "name": "CI"}
        assert json.loads(headless(binary, host).stdout)["device"] == host.device
        first = record(binary, SECRET, "UserPromptSubmit", prompt="first text")
        assert json.loads(first.stdout)["disposition"] == "spooled"
        spool = binary.home / "profile-spool"
        (guard,) = spool.glob("*.policy")
        (content,) = spool.glob("*.jsonl")
        original = content.read_bytes()
        assert (
            record(binary, new, "UserPromptSubmit", prompt="unknown bearer").returncode
            == 1
        )
        assert content.read_bytes() == original
        host.label = "Updated label"
        host.etag = '"changed"'
        host.device = {"id": "reauthorized", "name": "CI"}
        assert headless(binary, host, new).returncode == 0
        assert (
            record(binary, new, "UserPromptSubmit", prompt="verified bearer").returncode
            == 0
        )
        assert b"verified bearer" in content.read_bytes()
        assert b"unknown bearer" not in content.read_bytes()
        assert (
            record(
                binary,
                new,
                "PostToolUse",
                tool_name="shell",
                tool_response="private output",
            ).returncode
            == 0
        )
        assert b"private output" not in content.read_bytes()
        guard_bytes = guard.read_bytes()
        host.state_path = "/other-state"
        assert headless(binary, host, new).returncode == 0
        # #447, ruling D5: what was recorded for the earlier destination is held,
        # and the prompt opens a new span bound to the new one.
        moved = record(binary, new, "UserPromptSubmit", prompt="different destination")
        assert moved.returncode == 0
        assert json.loads(moved.stdout)["disposition"] == "spooled"
        held = spool / "held" / content.stem
        span = (held / "1.jsonl").read_bytes()
        assert span.split(b"\n", 1)[1].startswith(original)
        assert b"verified bearer" in span
        assert json.loads((held / "1.reason").read_text()) == {
            "reason": "policy_changed"
        }
        assert guard.read_bytes() != guard_bytes
        assert content.read_bytes().count(b"\n") == 1
        assert b"different destination" in content.read_bytes()
        host.locked = True
        assert headless(binary, host, new, "--capture", "on").returncode == 0
        assert (
            json.loads(
                record(binary, new, "UserPromptSubmit", prompt="disabled").stdout
            )["disposition"]
            == "capture_off_pending"
        )


def test_workspace_creation_preserves_manual_bindings(binary: Binary) -> None:
    with host_server() as host:
        host.create = True
        project = binary.directory / "checkout"
        project.mkdir()
        subprocess.run(["git", "init", "--quiet", str(project)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "remote",
                "add",
                "origin",
                "https://git.example.test/example/repo.git",
            ],
            check=True,
        )
        result = headless(
            binary, host, SECRET, "--workspace", "new", "--project", str(project)
        )
        assert result.returncode == 0, result.stdout
        assert json.loads(result.stdout)["project"]["profile"] == "created"
        _, _, _, data = next(call for call in host.calls if call[0] == "/workspaces")
        assert data["name"] == project.name and data["repository_key"].startswith(
            "sha256:"
        )
        assert "example" not in str(data)
        manual = binary.directory / "outside"
        manual.mkdir()
        assert binary.run("profile", "init", "mine", "--write").returncode == 0
        assert (
            binary.run(
                "profile",
                "bind",
                "mine",
                "--project",
                str(project),
                "--write",
                "--replace",
            ).returncode
            == 0
        )
        host.names = ("personal",)
        assert headless(binary, host).returncode == 0
        assert not (binary.home / "profiles/created.toml").exists()
        assert (
            json.loads(binary.run("profile", "show", "--project", str(project)).stdout)[
                "profile"
            ]
            == "mine"
        )


def test_device_and_discovery_failures_leave_no_connection(binary: Binary) -> None:
    with host_server() as host:
        host.endpoint = "https://other.example.test/machine"
        result = headless(binary, host)
        assert result.returncode == 1
        assert [call[0] for call in host.calls] == ["/host"]
        host.endpoint = None
        host.deny = True
        result = run(binary, "login", "--json", "--host", host.url + "/host", env=TRUST)
        assert result.returncode == 1 and "denied" in result.stdout
        assert not (binary.home / "login/credential.toml").exists()
        assert not (binary.home / "login/state.toml").exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix private credential storage")
def test_redo_finishes_credential_and_assignments(binary: Binary) -> None:
    with host_server() as host:
        binary.home.mkdir()
        obstruction = binary.home / "profiles"
        obstruction.mkdir(mode=0o500)
        if os.access(obstruction, os.W_OK):
            pytest.skip("test requires unprivileged file writes")
        result = run(
            binary,
            "login",
            "--host",
            host.url + "/host",
            "--workspace",
            "none",
            "--json",
            env=TRUST,
        )
        assert result.returncode == 1
        login = binary.home / "login"
        journal = login / "pending.toml"
        assert SECRET.encode() not in journal.read_bytes()
        assert (login / "credential.next.toml").exists() and not (
            login / "credential.toml"
        ).exists()
        obstruction.chmod(0o700)
        # A compatible manual writer adds unrelated configuration, then
        # occupies the name this interrupted installation intended to create.
        registry = binary.home / "profile-bindings.toml"
        registry.write_text(
            'binding_format = 1\ninstallation_id = "11111111-2222-4333-8444-555555555555"\n'
            '[profiles.manual.knowledge.endpoint]\nbackend = "file"\n'
            f"root = {json.dumps(str(binary.directory))}\n"
        )
        collision = obstruction / "personal.toml"
        collision.write_text(PROFILE)
        assert run(binary, "login", "--refresh", "--json", env=TRUST).returncode == 1
        assert collision.read_text() == PROFILE and journal.exists()
        collision.rename(obstruction / "manual.toml")
        result = run(binary, "login", "--refresh", "--json", env=TRUST)
        assert result.returncode == 0, result.stdout
        assert not journal.exists() and not (login / "credential.next.toml").exists()
        assert (
            tomllib.loads(registry.read_text())["profiles"]["manual"]["knowledge"][
                "endpoint"
            ]["backend"]
            == "file"
        )
        assert json.loads(binary.run("profile", "show", "manual").stdout)["ready"]
        assert json.loads(binary.run("status", "--json").stdout)["login"]["connected"]
        assert run(binary, "memory", "view", "/project", env=TRUST).returncode == 0


def test_session_start_refresh_cannot_enable_an_unseen_notice(binary: Binary) -> None:
    with host_server() as host:
        host.refresh_seconds = 1
        assert headless(binary, host).returncode == 0
        host.notice = "The host changed its recording notice."
        host.etag = '"changed-notice"'
        time.sleep(1.05)
        started = record(binary, SECRET, "SessionStart")
        assert started.returncode == 0, (started.stdout, started.stderr)
        machine_calls = [call for call in host.calls if call[0] == "/machine"]
        assert len(machine_calls) == 2 and machine_calls[-1][2] == '"1"'
        assert (
            json.loads(
                record(binary, SECRET, "UserPromptSubmit", prompt="private").stdout
            )["disposition"]
            == "capture_off"
        )
        refreshed = run(
            binary, "login", "--refresh", env={**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
        )
        assert refreshed.returncode == 0 and host.notice in refreshed.stderr
        assert (
            json.loads(
                record(binary, SECRET, "UserPromptSubmit", prompt="recorded").stdout
            )["disposition"]
            == "spooled"
        )
