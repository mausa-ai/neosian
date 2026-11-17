"""Managed recording consent, writer ownership and capture credential boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_machine_login import (
    SECRET,
    TRUST,
    headless,
    host_server,
    record,
)

ENV = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}


def assert_off(binary: Binary) -> None:
    status = json.loads(run(binary, "status", "--json", env=ENV).stdout)
    assert status["profile"]["capture"] == "off" and status["profile"]["ready"]
    event = record(binary, SECRET, "UserPromptSubmit", prompt="private prompt")
    assert json.loads(event.stdout)["disposition"] == "capture_off"
    assert not (binary.home / "profile-spool").exists()


def test_local_opt_out_while_host_is_offline(binary: Binary) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
    result = run(binary, "login", "--refresh", "--capture", "off", env=ENV)
    assert result.returncode == 1 and "Recording is off" in result.stderr
    assert_off(binary)
    assert run(binary, "login", "--refresh", "--capture", "on", env=ENV).returncode == 1
    assert_off(binary)


def test_local_opt_out_survives_reconciliation_conflict(binary: Binary) -> None:
    with host_server() as host:
        host.names = ("personal", "retired")
        assert headless(binary, host).returncode == 0
        checkout = binary.directory / "manual"
        checkout.mkdir()
        assert (
            binary.run(
                "profile", "bind", "retired", "--project", str(checkout), "--write"
            ).returncode
            == 0
        )
        host.names = ("personal",)
        result = run(binary, "login", "--refresh", "--capture", "off", env=ENV)
        assert result.returncode == 1 and "manual project binding" in result.stderr
        assert_off(binary)
        assert run(binary, "memory", "view", "/project", env=ENV).returncode == 0


def test_newer_local_off_wins_during_foreground_enable(binary: Binary) -> None:
    with host_server() as host, ThreadPoolExecutor(max_workers=1) as pool:
        assert headless(binary, host, SECRET, "--capture", "off").returncode == 0
        entered, resume = threading.Event(), threading.Event()
        host.hold = entered, resume
        earlier = pool.submit(
            run, binary, "login", "--refresh", "--capture", "on", "--json", env=ENV
        )
        try:
            assert entered.wait(5)
            later = run(binary, "login", "--refresh", "--capture", "off", env=ENV)
            assert later.returncode == 1 and "already running" in later.stderr
            assert_off(binary)
        finally:
            resume.set()
        result = earlier.result(timeout=5)
        assert result.returncode == 0 and json.loads(result.stdout)["capture"] == "off"
        assert_off(binary)
        host.hold = None
        assert (
            run(binary, "login", "--refresh", "--capture", "on", env=ENV).returncode
            == 0
        )
        assert (
            json.loads(record(binary, SECRET, "UserPromptSubmit", prompt="new").stdout)[
                "disposition"
            ]
            == "spooled"
        )


def test_skill_writer_and_manual_source_cannot_take_machine_authority(
    binary: Binary,
) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        profile = binary.home / "profiles/personal.toml"
        old = profile.read_bytes()
        result = run(
            binary,
            "skill",
            "select",
            "builtin:handoff",
            "--profile",
            "personal",
            "--write",
            env=ENV,
        )
        assert (
            result.returncode != 0 and "host-managed" in result.stdout + result.stderr
        )
        assert old == profile.read_bytes()
        assert binary.run("profile", "init", "manual", "--write").returncode == 0
        assert (
            binary.run(
                "profile",
                "bind",
                "manual",
                "--source",
                "workspace",
                "--url",
                host.url + "/unassigned",
                "--token-env",
                "NEOSIAN_LOGIN_TOKEN",
                "--write",
            ).returncode
            == 0
        )
        calls = len(host.calls)
        result = run(
            binary, "memory", "view", "/project", "--profile", "manual", env=ENV
        )
        assert (
            result.returncode != 0
            and "assigned profiles" in result.stdout + result.stderr
        )
        assert len(host.calls) == calls


def test_stdio_start_refreshes_clients_without_session_start(binary: Binary) -> None:
    with host_server() as host:
        host.refresh_seconds = 1
        assert headless(binary, host).returncode == 0
        host.etag = '"mcp-update"'
        host.label = "OpenCode workspace"
        time.sleep(1.05)
        result = run(binary, "mcp", "--actor", "mcp:opencode", env=ENV)
        assert result.returncode == 0, result.stderr
        assert [call[2] for call in host.calls if call[0] == "/machine"] == [
            None,
            '"1"',
        ]
        assert (
            json.loads(run(binary, "profile", "show", "personal", env=ENV).stdout)[
                "definition"
            ]["name"]
            == host.label
        )


def test_opt_out_catches_up_the_installed_profile_after_an_offline_refresh(
    binary: Binary,
) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        host.endpoint = "https://unavailable.example/machine"
        assert (
            run(binary, "login", "--refresh", "--capture", "off", env=ENV).returncode
            == 1
        )
        host.endpoint = None
        result = run(binary, "login", "--refresh", "--json", env=ENV)
        assert json.loads(result.stdout)["updated"] is True
        assert [c[2] for c in host.calls if c[0] == "/machine"] == [None, None]
        profile = tomllib.loads((binary.home / "profiles/personal.toml").read_text())
        registry = tomllib.loads((binary.home / "profile-bindings.toml").read_text())
        assert (
            profile["capture"]["mode"]
            == registry["profiles"]["personal"]["knowledge"]["capture"]
            == "off"
        )


@pytest.mark.skipif(
    os.name == "nt", reason="uses Unix directory permissions to interrupt rotation"
)
def test_interrupted_file_rotation_retires_the_old_device(binary: Binary) -> None:
    with host_server() as host:
        args = ("login", "--json", "--host", host.url + "/host", "--workspace", "none")
        assert run(binary, *args, env=TRUST).returncode == 0
        path = binary.home / "profiles"
        path.chmod(0o500)
        if os.access(path, os.W_OK):
            pytest.skip("test requires unprivileged file writes")
        new = "next-device-secret"
        host.tokens = SECRET, new
        host.issued_token = new
        try:
            failed = run(binary, *args, env=TRUST)
            assert failed.returncode == 1
            journal = (binary.home / "login/pending.toml").read_bytes()
            assert SECRET.encode() not in journal and new.encode() not in journal
            assert not [c for c in host.calls if c[0] == "/logout"]
        finally:
            path.chmod(0o700)
        assert run(binary, "login", "--refresh", env=TRUST).returncode == 0
        assert [c[1] for c in host.calls if c[0] == "/logout"] == [f"Bearer {SECRET}"]
        result = run(
            binary,
            "logout",
            "--json",
            env={**TRUST, "NEOSIAN_LOGIN_TOKEN": "unverified"},
        )
        assert json.loads(result.stdout)["revoked"]
        assert [c[1] for c in host.calls if c[0] == "/logout"][-1] == f"Bearer {new}"


def test_running_mcp_follows_verified_file_rotation_and_local_logout(
    binary: Binary,
) -> None:
    with host_server() as host:
        args = ("login", "--json", "--host", host.url + "/host", "--workspace", "none")
        assert run(binary, *args, env=TRUST).returncode == 0
        with subprocess.Popen(
            [str(binary.executable), "mcp", "--actor", "mcp:contract"],
            cwd=binary.directory,
            env={**binary.environment(), **TRUST},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as bridge:
            assert bridge.stdin and bridge.stdout
            inputs, outputs = bridge.stdin, bridge.stdout

            def rpc(number: int, method: str, params: dict[str, Any]) -> Any:
                inputs.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": number,
                            "method": method,
                            "params": params,
                        }
                    )
                    + "\n"
                )
                inputs.flush()
                return json.loads(outputs.readline())

            try:
                assert "result" in rpc(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "contract", "version": "1"},
                    },
                )
                inputs.write(
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}
                    )
                    + "\n"
                )
                inputs.flush()
                call = {
                    "name": "memory",
                    "arguments": {"command": "view", "path": "/project"},
                }
                assert not rpc(2, "tools/call", call)["result"].get("isError")
                new = "verified-successor"
                host.issued_token = new
                host.tokens = SECRET, new
                assert run(binary, *args, env=TRUST).returncode == 0
                assert not rpc(3, "tools/call", call)["result"].get("isError")
                assert host.calls[-1][1] == f"Bearer {new}"
                host.revocation_succeeds = False
                assert run(binary, "logout", "--json", env=TRUST).returncode == 0
                previous = len(host.calls)
                failed = rpc(4, "tools/call", call)
                assert failed["result"]["isError"] and len(host.calls) == previous
            finally:
                inputs.close()
                bridge.wait(timeout=5)
