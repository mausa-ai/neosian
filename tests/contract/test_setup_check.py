"""Setup checks exercise the binary without touching selected stores or clients."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.contract.profile_support import bind, install, run
from tests.contract.support import Binary, package_version


def files(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def path_text(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.removeprefix("\\\\?\\")))


def setup(binary: Binary, client: str = "pi", *args: str) -> tuple[str, ...]:
    target = binary.directory / client
    target.mkdir(exist_ok=True)
    options = ("--client", client, "--at", f"{client}={target}", *args)
    result = run(binary, "setup", *options, "--write", "--json", env={"PATH": ""})
    assert result.returncode == 0, result.stdout + result.stderr
    return options


def assert_probe(report: Any, binary: Binary) -> None:
    assert Path(report["probe"]["executable"]).samefile(binary.executable)
    assert report["probe"] == {
        "success": True,
        "initialize": True,
        "tools": True,
        "write": True,
        "read": True,
        "storage": "temporary_files",
        "executable": report["probe"]["executable"],
    }
    assert report["client_connection"] == "not_observed"
    assert report["hook_delivery"] == "not_observed"


@pytest.mark.parametrize("client", ["pi", "cursor", "muse-code", "opencode"])
def test_check_verifies_isolated_mcp_and_keeps_configuration(
    binary: Binary, client: str
) -> None:
    store = binary.directory / "unopened storage"
    options = setup(binary, client, "--root", str(store))
    temporary = binary.directory / "temporary"
    temporary.mkdir()
    before = files(binary.directory)
    result = run(
        binary,
        "setup",
        *options,
        "--check",
        "--json",
        env={
            "PATH": "",
            "TMPDIR": str(temporary),
            "TEMP": str(temporary),
            "TMP": str(temporary),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["success"] is True and report["issues"] == []
    assert_probe(report, binary)
    installation = report["installation"]
    assert Path(installation["running"]).samefile(binary.executable)
    assert Path(installation["selected"]["command"]).samefile(binary.executable)
    assert Path(installation["selected"]["resolved"]).samefile(binary.executable)
    assert installation == {
        "running": installation["running"],
        "version": package_version(),
        "selected": {
            "command": installation["selected"]["command"],
            "resolved": installation["selected"]["resolved"],
            "matches_selected": True,
        },
        "path_executables": [],
        "warnings": [],
    }
    for registration in report["clients"][0]["registrations"].values():
        assert registration["executable"]["matches_selected"] is True
        assert path_text(registration["storage"]["location"]) == path_text(str(store))
        assert registration["storage"]["capture"] == "enabled"
        assert registration["storage"]["access"] == "not_checked"
    assert files(binary.directory) == before
    assert not store.exists() and not binary.home.exists()
    assert list(temporary.iterdir()) == []


def test_check_diagnoses_missing_configuration_and_does_not_write(
    binary: Binary,
) -> None:
    before = files(binary.directory)
    result = binary.run("setup", "--client", "pi", "--check", "--json")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert_probe(report, binary)
    assert len(report["issues"]) == 2
    assert all("registration is missing or unreadable" in s for s in report["issues"])
    assert files(binary.directory) == before
    assert not binary.home.exists()
    result = binary.run("setup", "--client", "pi", "--check", "--write")
    assert result.returncode == 2
    assert files(binary.directory) == before


def test_check_reports_storage_mismatch(binary: Binary) -> None:
    options = setup(binary)
    path = binary.directory / "pi" / "mcp.json"
    document = json.loads(path.read_text())
    argv = document["mcpServers"]["neosian-memory"]["args"]
    argv[argv.index("--root") + 1] = str(binary.directory / "different")
    path.write_text(json.dumps(document))
    result = binary.run("setup", *options, "--check", "--json")
    assert result.returncode == 1
    assert "MCP and hooks select different storage" in result.stdout
    assert not (binary.directory / "different").exists()


def test_diagnostics_do_not_repeat_inline_credentials(binary: Binary) -> None:
    options = setup(binary)
    path = binary.directory / "pi" / "mcp.json"
    document = json.loads(path.read_text())
    document["mcpServers"]["neosian-memory"]["args"] = [
        "mcp",
        "--url",
        "https://user:PRIVATE_TEST_SECRET@memory.invalid/?key=PRIVATE_TEST_SECRET",
    ]
    path.write_text(json.dumps(document))
    for args in [("setup", *options, "--check"), ("status", *options[2:4])]:
        result = run(
            binary, *args, "--json", env={"NEOSIAN_CLIENT_TOKEN": "PRIVATE_TEST_SECRET"}
        )
        assert result.returncode == (1 if args[0] == "setup" else 0)
        assert "PRIVATE_TEST_SECRET" not in result.stdout + result.stderr
        report = json.loads(result.stdout)
        client = next(c for c in report["clients"] if c["client"] == "pi")
        assert client["registrations"]["mcp"]["storage"]["ready"] is False


def test_check_keeps_capture_off_and_reports_missing_credentials(
    binary: Binary,
) -> None:
    install(binary)
    bind(binary, "--url", "http://127.0.0.1:1", "--token-env", "SETUP_TEST_TOKEN")
    options = setup(binary, "pi", "--profile", "portable")
    before = files(binary.directory)
    missing = binary.run("setup", *options, "--check", "--json")
    assert missing.returncode == 1
    report = json.loads(missing.stdout)
    assert report["storage"]["profile"]["missing_environment"] == ["SETUP_TEST_TOKEN"]
    present = run(
        binary,
        "setup",
        *options,
        "--check",
        "--json",
        env={"SETUP_TEST_TOKEN": "PRIVATE_TEST_SECRET"},
    )
    assert present.returncode == 0, present.stdout + present.stderr
    assert "PRIVATE_TEST_SECRET" not in present.stdout + present.stderr
    report = json.loads(present.stdout)
    assert report["storage"]["capture"] == "off"
    assert report["storage"]["access"] == "not_checked"
    assert_probe(report, binary)
    assert files(binary.directory) == before


def test_check_isolates_probe_from_ambient_postgres(binary: Binary) -> None:
    target = binary.directory / "pi"
    target.mkdir()
    env = {"NEOSIAN_POSTGRES_DSN": "postgres://test:PRIVATE_TEST_SECRET@127.0.0.1:1/no"}
    args = ("setup", "--client", "pi", "--at", f"pi={target}", "--json")
    saved = run(binary, *args, "--write", env=env)
    assert saved.returncode == 0, saved.stdout + saved.stderr
    result = run(binary, *args, "--check", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRIVATE_TEST_SECRET" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["storage"]["kind"] == "postgres"
    assert report["storage"]["location"] is None
    assert_probe(report, binary)


@pytest.mark.skipif(os.name == "nt", reason="Unix executable/symlink contract")
def test_check_compares_paths_without_executing_registered_commands(
    binary: Binary,
) -> None:
    other = binary.directory / "neosian"
    marker = binary.directory / "executed"
    other.write_text(f"#!/bin/sh\n: > '{marker}'\n")
    other.chmod(0o700)
    options = setup(binary, "pi", "--binary", str(other))
    result = run(
        binary,
        "setup",
        *options,
        "--check",
        "--json",
        env={"PATH": str(binary.directory)},
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["installation"]["path_executables"] == [str(other)]
    assert len(report["installation"]["warnings"]) == 1
    assert any("not the running binary" in issue for issue in report["issues"])
    assert_probe(report, binary)
    assert not marker.exists()
    stale = run(binary, "setup", *options[:4], "--check", "--json")
    assert stale.returncode == 1
    assert "executable differs from the selected binary" in stale.stdout
    other.chmod(0o600)
    missing = run(binary, "setup", *options[:4], "--check", "--json")
    assert missing.returncode == 1
    assert "executable does not resolve" in missing.stdout
    other.unlink()
    other.symlink_to(binary.executable)
    # Symlinks to the same binary do not count as another installation.
    result = run(
        binary,
        "setup",
        *options,
        "--check",
        "--json",
        env={"PATH": str(binary.directory)},
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["installation"]["warnings"] == []


def test_status_reports_separate_registration_choices(binary: Binary) -> None:
    options = setup(binary)
    result = run(binary, "status", *options[2:4], "--json", env={"PATH": ""})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert Path(report["installation"]["running"]).samefile(binary.executable)
    client = next(c for c in report["clients"] if c["client"] == "pi")
    assert set(client["registrations"]) == {"mcp", "hooks"}
    for registration in client["registrations"].values():
        assert registration["executable"]["matches_selected"] is True
        assert registration["storage"]["kind"] == "files"
    text = binary.run("status", *options[2:4])
    assert text.returncode == 0
    assert "running" in text.stdout and "capture enabled" in text.stdout


def test_probe_failure_is_not_reported_as_success(binary: Binary) -> None:
    options = setup(binary)
    invalid = binary.directory / "not a directory"
    invalid.write_text("keep")
    before = files(binary.directory)
    result = run(
        binary,
        "setup",
        *options,
        "--check",
        "--json",
        env={"TMPDIR": str(invalid), "TEMP": str(invalid), "TMP": str(invalid)},
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["probe"]["success"] is False
    assert report["probe"]["error"]
    assert files(binary.directory) == before


@pytest.mark.parametrize("client", ["claude-code", "codex"])
def test_check_reads_client_owned_registrations(binary: Binary, client: str) -> None:
    target = binary.directory / client
    target.mkdir()
    command = str(binary.executable)
    argv = ["mcp", "--root", str(binary.home)]
    if client == "codex":
        (target / "config.toml").write_text(
            "[mcp_servers.neosian-memory]\n"
            f"command = {json.dumps(command)}\nargs = {json.dumps(argv)}\n"
        )
    else:
        (target / ".claude.json").write_text(
            json.dumps(
                {"mcpServers": {"neosian-memory": {"command": command, "args": argv}}}
            )
        )
    hook = run(
        binary,
        "record",
        "install",
        "--client",
        client,
        "--write",
        env={"CODEX_HOME" if client == "codex" else "CLAUDE_CONFIG_DIR": str(target)},
    )
    assert hook.returncode == 0, hook.stderr
    before = files(binary.directory)
    result = binary.run(
        "setup", "--client", client, "--at", f"{client}={target}", "--check", "--json"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_probe(json.loads(result.stdout), binary)
    assert files(binary.directory) == before
