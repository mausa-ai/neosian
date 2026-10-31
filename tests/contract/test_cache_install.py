"""Public setup and statusline contracts for Claude's optional host integration."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.contract.support import ROOT, Binary

PLUGIN = "neosian-cache@neosian-local"


def host(binary: Binary) -> tuple[Path, dict[str, str]]:
    """A CLI-only double: no implementation imports and no model session."""
    directory = binary.directory / "client configuration"
    directory.mkdir()
    commands = binary.directory / "commands"
    commands.mkdir()
    script = commands / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,sys\n"
        "args=sys.argv[1:]\n"
        "base=pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'])\n"
        "with (base/'commands.jsonl').open('a') as out: out.write(json.dumps(args)+'\\n')\n"
        "if args[:2]==['plugin','install']:\n"
        " path=pathlib.Path.cwd()/'.claude/settings.json'\n"
        " doc=json.loads(path.read_text())\n"
        f" doc.setdefault('enabledPlugins',{{}})[{PLUGIN!r}]=True\n"
        " path.write_text(json.dumps(doc))\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return directory, {**binary.environment(), "PATH": str(commands)}


def setup(
    binary: Binary, client: Path, env: dict[str, str], *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary.executable),
            "setup",
            "--client",
            "claude-code",
            "--level",
            "project",
            "--at",
            f"claude-code={client}",
            "--json",
            *extra,
        ],
        cwd=binary.directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="Unix CLI double")
def test_cache_setup_uses_public_cli_and_preserves_statusline(binary: Binary) -> None:
    client, env = host(binary)
    config = binary.directory / ".claude/settings.json"
    config.parent.mkdir()
    original = {
        "type": "command",
        "command": "printf 'MY STATUS'",
        "padding": 2,
        "refreshInterval": 10,
    }
    config.write_text(json.dumps({"statusLine": original, "foreign": {"keep": True}}))
    preview = setup(binary, client, env)
    assert preview.returncode == 0, preview.stdout
    root = config.parent / "neosian-cache"
    assert not root.exists() and not (client / "commands.jsonl").exists()
    for _ in range(2):
        result = setup(binary, client, env, "--write")
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)["clients"][0]["cache"]
        assert report["applied"] is True and report["preserves_statusline"] is True
        saved = json.loads((root / "statusline.json").read_text())
        assert saved["original"] == original
        configured = json.loads(config.read_text())
        assert configured["foreign"] == {"keep": True}
        assert configured["statusLine"] == {**original, "command": saved["wrapper"]}
        assert configured["enabledPlugins"][PLUGIN] is True
    calls = [
        json.loads(line)
        for line in (client / "commands.jsonl").read_text().splitlines()
    ]
    assert (
        calls
        == [
            ["plugin", "marketplace", "add", str(root), "--scope", "project"],
            ["plugin", "install", PLUGIN, "--scope", "project"],
        ]
        * 2
    )
    for name in (
        "hooks/register.js",
        "hooks/evidence.js",
        "hooks/display.js",
        "assets/refresh.md",
        ".claude-plugin/plugin.json",
    ):
        assert (root / "plugin" / name).read_bytes() == (
            ROOT / "assets/clients/claude-cache" / name
        ).read_bytes()
    assert json.loads((root / "plugin/bridge.json").read_text()) == {
        "observations": str(root / "observations")
    }
    assert not binary.home.exists()
    healthy = json.loads(setup(binary, client, env, "--check").stdout)
    level = healthy["clients"][0]["cache"]["levels"][1]
    assert level["current"] is True and level["observation_configured"] is True
    assert level["runtime"] == "not_observed"
    bridge = root / "plugin/bridge.json"
    original_bridge = bridge.read_text()
    bridge.write_text('{"observations":"/wrong-location"}')
    broken = setup(binary, client, env, "--check")
    assert broken.returncode == 1
    assert any(
        "cache (project)" in issue for issue in json.loads(broken.stdout)["issues"]
    )
    bridge.write_text(original_bridge)
    # Turning it off restores the exact prior value and stays off on setup.
    for extra in [("--claude-cache", "off"), ()]:
        result = setup(binary, client, env, "--write", *extra)
        assert result.returncode == 0, result.stdout
        configured = json.loads(config.read_text())
        assert configured["statusLine"] == original
        assert configured["enabledPlugins"][PLUGIN] is False
    assert len((client / "commands.jsonl").read_text().splitlines()) == 4


@pytest.mark.skipif(os.name == "nt", reason="Unix CLI double")
def test_cache_refuses_uncomposable_settings_before_writing(binary: Binary) -> None:
    client, env = host(binary)
    config = binary.directory / ".claude/settings.json"
    config.parent.mkdir()
    body = '{"statusLine":{"type":"future-host-type","command":"preserve"}}'
    config.write_text(body)
    result = setup(binary, client, env, "--write")
    assert result.returncode == 1
    assert config.read_text() == body
    assert not (binary.directory / ".mcp.json").exists()
    assert not (client / "commands.jsonl").exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix CLI double")
def test_cache_default_observer_interval_and_disable_preserve_absence(
    binary: Binary,
) -> None:
    client, env = host(binary)
    config = binary.directory / ".claude/settings.json"
    result = setup(binary, client, env, "--write")
    assert result.returncode == 0, result.stdout
    assert json.loads(config.read_text())["statusLine"]["refreshInterval"] == 30
    result = setup(binary, client, env, "--claude-cache", "off", "--write")
    assert result.returncode == 0, result.stdout
    assert "statusLine" not in json.loads(config.read_text())


@pytest.mark.skipif(os.name == "nt", reason="Unix CLI double")
def test_cache_project_disable_restores_statusline_inheritance(binary: Binary) -> None:
    client, env = host(binary)
    user_config = client / "settings.json"
    original = {"type": "command", "command": "printf 'INHERITED'"}
    body = json.dumps({"statusLine": original})
    user_config.write_text(body)
    config = binary.directory / ".claude/settings.json"
    for _ in range(2):
        result = setup(binary, client, env, "--write")
        assert result.returncode == 0, result.stdout
        saved = json.loads(
            (config.parent / "neosian-cache/statusline.json").read_text()
        )
        assert saved["original"] == original and saved["restore"] is None
    result = setup(binary, client, env, "--claude-cache", "off", "--write")
    assert result.returncode == 0, result.stdout
    assert "statusLine" not in json.loads(config.read_text())
    assert user_config.read_text() == body


def payload() -> dict[str, Any]:
    return {
        "session_id": "test-session",
        "version": "2.1.314",
        "model": {"id": "claude-test"},
        "prompt_cache": {
            "warm": True,
            "caching_observed": True,
            "ttl": "1h",
            "expires_at": int(time.time()) + 3600,
            "requests": 1,
        },
        "context_window": {
            "current_usage": {
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 17000,
            }
        },
        "rate_limits": {
            "five_hour": {"used_percentage": 15, "resets_at": int(time.time()) + 5000}
        },
        "private_host_field": "DO_NOT_PERSIST_THIS",
        "transcript_path": "/private/user/history.jsonl",
    }


@pytest.mark.skipif(os.name == "nt", reason="Unix prior statusline")
def test_bridge_forwards_original_input_and_only_persists_allowlisted_fields(
    binary: Binary,
) -> None:
    root = binary.directory / "bridge"
    root.mkdir()
    original = binary.directory / "previous.py"
    original.write_text(
        "import json,sys\np=json.load(sys.stdin)\nprint('EXISTING:'+p['private_host_field'],end='')\n"
    )
    (root / "statusline.json").write_text(
        json.dumps(
            {"original": {"command": shlex.join([sys.executable, str(original)])}}
        )
    )
    observations = root / "observations"
    observations.mkdir()
    receipt = observations / "test-session.refresh.json"
    receipt.write_text(
        json.dumps(
            {"session_id": "test-session", "observed_at_ms": int(time.time() * 1000)}
        )
    )
    # A broken memory home must not affect the observer or the prior statusline.
    binary.home.write_text("NOT A MEMORY STORE")
    result = binary.run(
        "statusline",
        "--client",
        "claude-code",
        "--bridge",
        str(root),
        stdin=json.dumps(payload()),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "EXISTING:DO_NOT_PERSIST_THIS"
    raw = (observations / "test-session.json").read_text()
    data = json.loads(raw)
    assert data["format"] == 1 and data["ttl"] == "1h" and data["subscription"] is True
    assert data["last_main_cache_read_tokens"] == 2000
    assert "DO_NOT_PERSIST_THIS" not in raw and "history.jsonl" not in raw
    assert set(data) == {
        "format",
        "session_id",
        "version",
        "model",
        "observed_at_ms",
        "subscription",
        "state",
        "ttl",
        "expires_at",
        "remaining_seconds",
        "recache_tokens_if_cold",
        "last_main_cache_read_tokens",
        "last_main_cache_write_tokens",
        "main_requests",
        "main_misses",
    }
    assert stat.S_IMODE((observations / "test-session.json").stat().st_mode) == 0o600
    assert binary.home.read_text() == "NOT A MEMORY STORE"


@pytest.mark.parametrize(
    "session", ["../escape", "/absolute", "a/b", "bad\u001b[2J", "x" * 129]
)
def test_bridge_rejects_unsafe_session_names(binary: Binary, session: str) -> None:
    root = binary.directory / "bridge"
    data = payload()
    data["session_id"] = session
    result = binary.run(
        "statusline",
        "--client",
        "claude-code",
        "--bridge",
        str(root),
        stdin=json.dumps(data),
    )
    assert result.returncode == 0
    assert not root.exists()
    assert session not in result.stdout + result.stderr


def test_bridge_reports_missing_mod_without_an_api_call(binary: Binary) -> None:
    result = binary.run(
        "statusline",
        "--client",
        "claude-code",
        "--bridge",
        str(binary.directory / "bridge"),
        stdin=json.dumps(payload()),
    )
    assert result.returncode == 0
    assert "keep-warm not observed" in result.stdout
    assert not binary.home.exists()


@pytest.mark.parametrize(
    "age,session",
    [(120, "test-session"), (0, "different-session"), (-60, "test-session")],
)
def test_bridge_reports_stale_or_mismatched_heartbeat(
    binary: Binary, age: int, session: str
) -> None:
    root = binary.directory / "bridge"
    observations = root / "observations"
    observations.mkdir(parents=True)
    (observations / "test-session.refresh.json").write_text(
        json.dumps(
            {"session_id": session, "observed_at_ms": int((time.time() - age) * 1000)}
        )
    )
    result = binary.run(
        "statusline",
        "--client",
        "claude-code",
        "--bridge",
        str(root),
        stdin=json.dumps(payload()),
    )
    assert result.returncode == 0
    assert "keep-warm not observed" in result.stdout
