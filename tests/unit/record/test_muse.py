"""Muse installations preserve settings and pass credentials only by name."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from neosian._cli.setup import run_setup
from neosian._cli.status import client_status
from neosian._foundation.mcp.install import run_install as mcp_install
from neosian._foundation.record.install import hook_fragment, run_install
from neosian._foundation.shared.client_config import Environment


@pytest.fixture
def context(tmp_path: Path) -> Environment:
    (tmp_path / "config" / "muse").mkdir(parents=True)
    (tmp_path / "project").mkdir()
    return Environment(
        home=tmp_path,
        cwd=tmp_path / "project",
        platform="darwin",
        executable=sys.executable,
        env={"XDG_CONFIG_HOME": str(tmp_path / "config")},
    )


def invoke(
    context: Environment,
    *args: str,
    kind: str = "hooks",
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    run = {"hooks": run_install, "mcp": mcp_install, "setup": run_setup}[kind]
    code = run(
        ["--client", "muse-code", "--json", *args],
        (
            env
            if env is not None
            else {
                "NEOSIAN_HOME": str(context.home / "nh"),
                "NEOSIAN_CLIENT_TOKEN": "test-token",
            }
        ),
        context=context,
        out=out,
        err=io.StringIO(),
    )
    return code, json.loads(out.getvalue())


def settings(context: Environment) -> Path:
    return Path(context.env["XDG_CONFIG_HOME"]) / "muse" / "settings.json"


def test_setup_shares_settings_preserves_values_and_is_idempotent(
    context: Environment,
) -> None:
    path = settings(context)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "theme": "dark",
                "private": "hidden",
                "hooks": {"SessionEnd": [{"hooks": [{"command": "other"}]}]},
            }
        )
    )
    before = path.read_bytes()
    code, preview = invoke(context, kind="setup")
    assert code == 0 and path.read_bytes() == before
    assert "hidden" not in json.dumps(preview)
    for _ in range(2):
        code, result = invoke(context, "--write", kind="setup")
        assert code == 0 and "hidden" not in json.dumps(result)
    doc = json.loads(path.read_text())
    assert doc["theme"] == "dark" and doc["private"] == "hidden"
    assert len(doc["hooks"]["Stop"]) == 1 and "SessionEnd" in doc["hooks"]
    assert "--mount" not in doc["mcpServers"]["neosian-memory"]["args"]
    assert list(context.cwd.iterdir()) == []
    status = client_status("muse-code", context)
    assert status.hooks_present and status.mcp_registered and status.level == "user"


def test_new_settings_and_project_files(context: Environment) -> None:
    code, _ = invoke(context, "--level", "project", "--write", kind="setup")
    assert code == 0 and not settings(context).exists()
    hooks = json.loads((context.cwd / ".muse" / "hooks.json").read_text())
    assert "--mount" in hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert (context.cwd / ".mcp.json").is_file()
    code, _ = invoke(context, "--write", kind="setup")
    assert code == 0
    assert json.loads(settings(context).read_text())["schema_version"] == 1
    assert json.loads((context.cwd / ".muse" / "hooks.json").read_text())["hooks"] == {}
    # The shared MCP entry is retained for Claude Code.
    assert (context.cwd / ".mcp.json").is_file()
    assert client_status("muse-code", context).mcp_shadowed_by == str(
        context.cwd / ".mcp.json"
    )


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema_version": True},
        {"schema_version": 2},
        {"schema_version": 1, "hooks": []},
    ],
)
def test_invalid_settings_refuse_before_either_setup_half(
    context: Environment, document: dict[str, Any]
) -> None:
    path = settings(context)
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    assert invoke(context, "--write", kind="setup")[0] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "store_args,env,name",
    [
        (
            ("--url", "http://state"),
            {"NEOSIAN_CLIENT_TOKEN": "sensitive"},
            "NEOSIAN_CLIENT_TOKEN",
        ),
        (
            (),
            {"NEOSIAN_POSTGRES_DSN": "postgresql://sensitive"},
            "NEOSIAN_POSTGRES_DSN",
        ),
    ],
)
def test_managed_transitions_and_credential_references(
    context: Environment,
    store_args: tuple[str, ...],
    env: dict[str, str],
    name: str,
) -> None:
    assert invoke(context, "--write", kind="setup")[0] == 0
    code, preview = invoke(context, *store_args, kind="setup", env=env)
    assert code == 0 and "sensitive" not in json.dumps(preview)
    code, result = invoke(context, *store_args, "--write", kind="setup", env=env)
    assert code == 0 and "sensitive" not in json.dumps(result)
    path = settings(context)
    doc = json.loads(path.read_text())
    assert doc["managed_hooks_env_vars"] == [name]
    assert doc["hooks"] == {}
    entry = doc["mcpServers"]["neosian-memory"]
    assert entry["env"] == {name: "${" + name + "}"}
    managed = path.parent / "neosian-hooks.json"
    status = client_status("muse-code", context)
    assert status.level == "user" and status.hook_files == (str(managed),)
    assert invoke(context, "--write", kind="setup")[0] == 0
    assert json.loads(managed.read_text())["hooks"] == {}
    assert client_status("muse-code", context).hook_files == (str(path),)


def test_project_credentials_refused_before_mcp_write(context: Environment) -> None:
    code, result = invoke(
        context, "--level", "project", "--url", "http://state", "--write", kind="setup"
    )
    assert code == 1 and "--level user" in str(result)
    assert list(context.cwd.iterdir()) == [] and not settings(context).exists()


def test_foreign_managed_file_and_environment_names_preserved(
    context: Environment,
) -> None:
    path = settings(context)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "managed_hooks_path": "company.json",
                "managed_hooks_env_vars": ["COMPANY_TOKEN"],
            }
        )
    )
    before = path.read_bytes()
    assert invoke(context, "--url", "http://state", "--write", kind="setup")[0] == 1
    assert path.read_bytes() == before
    path.write_text(
        json.dumps({"schema_version": 1, "managed_hooks_env_vars": ["COMPANY_TOKEN"]})
    )
    assert invoke(context, "--url", "http://state", "--write")[0] == 0
    assert json.loads(path.read_text())["managed_hooks_env_vars"] == [
        "COMPANY_TOKEN",
        "NEOSIAN_CLIENT_TOKEN",
    ]


def test_project_refused_beside_managed_and_all_sources_reported(
    context: Environment,
) -> None:
    assert invoke(context, "--url", "http://state", "--write")[0] == 0
    assert invoke(context, "--level", "project", "--write")[0] == 1
    path = settings(context)
    doc = json.loads(path.read_text())
    doc.update(hook_fragment("/python -m neosian.record --agent muse-code"))
    path.write_text(json.dumps(doc))
    assert len(client_status("muse-code", context).hook_files) == 2


def test_default_home_and_missing_client(context: Environment) -> None:
    default = replace(context, env={})
    assert invoke(default)[0] == 1
    assert not (context.home / ".config" / "muse").exists()
    (context.home / ".config" / "muse").mkdir(parents=True)
    assert invoke(default, "--write")[0] == 0
    assert (context.home / ".config" / "muse" / "settings.json").is_file()


def test_preview_never_displaces_when_target_is_invalid(context: Environment) -> None:
    assert invoke(context, "--level", "project", "--write")[0] == 0
    project = context.cwd / ".muse" / "hooks.json"
    before = project.read_bytes()
    settings(context).write_text('{"schema_version":1,"hooks":[]}')
    assert invoke(context, "--write")[0] == 1
    assert project.read_bytes() == before


def test_setup_text_never_claims_an_unapplied_half(context: Environment) -> None:
    out = io.StringIO()
    code = run_setup(
        [
            "--client",
            "muse-code",
            "--level",
            "project",
            "--url",
            "http://state",
            "--write",
        ],
        {"NEOSIAN_CLIENT_TOKEN": "test-token"},
        context=context,
        out=out,
        err=io.StringIO(),
    )
    assert code == 1 and "not applied" in out.getvalue()
    assert "updated" not in out.getvalue() and "created" not in out.getvalue()
    assert list(context.cwd.iterdir()) == []
