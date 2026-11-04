"""Native skill setup must be discoverable, repairable and preserve user files."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.profile_support import run
from tests.contract.support import ROOT, Binary
from tests.contract.test_setup_check import files

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI fixture")
ASSET = ROOT / "assets/clients/codex/skills/neosian-upkeep"


@pytest.fixture
def client(binary: Binary) -> tuple[Path, dict[str, str]]:
    target = binary.directory / "codex home"
    target.mkdir()
    commands = binary.directory / "commands"
    commands.mkdir()
    command = commands / "codex"
    command.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:5] == ['mcp', 'add', 'neosian-memory', '--']\n"
        "base = pathlib.Path(os.environ['CODEX_HOME'])\n"
        "(base / 'config.toml').write_text('[mcp_servers.neosian-memory]\\n'\n"
        "    + 'command = ' + json.dumps(sys.argv[5]) + '\\nargs = '\n"
        "    + json.dumps(sys.argv[6:]) + '\\n')\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    return target, {"PATH": str(commands), "CODEX_HOME": str(target)}


def setup(
    binary: Binary, client: tuple[Path, dict[str, str]], *args: str
) -> subprocess.CompletedProcess[str]:
    return run(binary, "setup", "--client", "codex", *args, "--json", env=client[1])


def applied(binary: Binary, client: tuple[Path, dict[str, str]], *args: str) -> Any:
    result = setup(binary, client, *args, "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["clients"][0]["skills"]


def test_install_preview_repeat_and_check(
    binary: Binary, client: tuple[Path, dict[str, str]]
) -> None:
    before = files(binary.directory)
    preview = setup(binary, client)
    assert preview.returncode == 0, preview.stderr
    skill = json.loads(preview.stdout)["clients"][0]["skills"]
    assert skill["action"] == "install" and skill["written"] is False
    assert files(binary.directory) == before
    report = applied(binary, client)
    assert report["installed"] and report["current"] and report["applied"]
    assert report["discovery"] == "not_observed"
    native = client[0] / "skills/neosian-upkeep"
    for relative in ["SKILL.md", "agents/openai.yaml"]:
        assert (native / relative).read_bytes() == (ASSET / relative).read_bytes()
    assert "load_skill" in (native / "SKILL.md").read_text()
    assert not binary.home.exists()
    assert not (binary.directory / ".codex").exists()
    before = files(binary.directory)
    assert applied(binary, client)["action"] == "unchanged"
    assert files(binary.directory) == before
    checked = setup(binary, client, "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["issues"] == []
    assert files(binary.directory) == before


def test_at_selects_only_the_named_codex_home(
    binary: Binary, client: tuple[Path, dict[str, str]]
) -> None:
    other = binary.directory / "other codex"
    other.mkdir()
    applied(binary, client, "--at", f"codex={other}")
    assert (other / "skills/neosian-upkeep/SKILL.md").is_file()
    assert list(client[0].iterdir()) == []


@pytest.mark.parametrize(
    "damage", ["missing", "old", "partial_install", "partial_update"]
)
def test_checks_find_and_setup_repairs_managed_entries(
    binary: Binary, client: tuple[Path, dict[str, str]], damage: str
) -> None:
    applied(binary, client)
    native = client[0] / "skills/neosian-upkeep"
    receipt = client[0] / "neosian-skills.json"
    if damage in {"missing", "partial_install"}:
        (native / "SKILL.md").unlink()
        if damage == "missing":
            (native / "agents/openai.yaml").unlink()
    else:
        saved = json.loads(receipt.read_text())
        for name in ["SKILL.md", "agents/openai.yaml"]:
            old = b"previous installed revision\n"
            saved["files"][name] = hashlib.sha256(old).hexdigest()
            if name == "SKILL.md" or damage == "old":
                (native / name).write_bytes(old)
        receipt.write_text(json.dumps(saved))
    before = files(binary.directory)
    checked = setup(binary, client, "--check")
    assert checked.returncode == 1, checked.stdout
    assert any("codex skills:" in s for s in json.loads(checked.stdout)["issues"])
    assert files(binary.directory) == before
    assert applied(binary, client)["current"]
    assert setup(binary, client, "--check").returncode == 0


def test_disable_preserves_foreign_files_and_opt_out(
    binary: Binary, client: tuple[Path, dict[str, str]]
) -> None:
    applied(binary, client)
    native = client[0] / "skills/neosian-upkeep"
    extra = native / "notes.txt"
    extra.write_text("user notes")
    sibling = client[0] / "skills/my-skill"
    sibling.mkdir()
    (sibling / "SKILL.md").write_text("user skill")
    before = files(binary.directory)
    preview = setup(binary, client, "--codex-skills", "off")
    assert json.loads(preview.stdout)["clients"][0]["skills"]["action"] == "remove"
    assert files(binary.directory) == before
    assert applied(binary, client, "--codex-skills", "off")["enabled"] is False
    assert not (native / "SKILL.md").exists()
    assert extra.read_text() == "user notes"
    assert (sibling / "SKILL.md").read_text() == "user skill"
    assert applied(binary, client)["enabled"] is False
    assert setup(binary, client, "--check").returncode == 0
    assert applied(binary, client, "--codex-skills", "on")["current"] is True
    assert extra.read_text() == "user notes"


@pytest.mark.parametrize(
    "damage", ["unmanaged", "body", "metadata", "receipt", "future"]
)
@pytest.mark.parametrize("mode", ["on", "off"])
def test_conflicts_refuse_before_any_configuration_write(
    binary: Binary, client: tuple[Path, dict[str, str]], damage: str, mode: str
) -> None:
    applied(binary, client)
    native = client[0] / "skills/neosian-upkeep"
    receipt = client[0] / "neosian-skills.json"
    if damage == "unmanaged":
        receipt.unlink()
    elif damage in {"body", "metadata"}:
        (
            native / ("SKILL.md" if damage == "body" else "agents/openai.yaml")
        ).write_text("user edits")
    elif damage == "receipt":
        receipt.write_text("invalid receipt")
    else:
        saved = json.loads(receipt.read_text())
        saved["format"] = 2
        receipt.write_text(json.dumps(saved))
    before = files(binary.directory)
    result = setup(binary, client, "--codex-skills", mode, "--write")
    assert result.returncode == 1, result.stdout
    assert json.loads(result.stdout)["clients"][0]["skills"]["success"] is False
    assert files(binary.directory) == before


@pytest.mark.parametrize("where", ["skills", "root", "agents", "body", "receipt"])
def test_symlinks_are_not_followed(
    binary: Binary, client: tuple[Path, dict[str, str]], where: str
) -> None:
    applied(binary, client)
    native = client[0] / "skills/neosian-upkeep"
    path = {
        "skills": client[0] / "skills",
        "root": native,
        "agents": native / "agents",
        "body": native / "SKILL.md",
        "receipt": client[0] / "neosian-skills.json",
    }[where]
    outside = binary.directory / "foreign"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    before = files(binary.directory)
    for mode in ["on", "off"]:
        result = setup(binary, client, "--codex-skills", mode, "--write")
        assert result.returncode == 1
        assert "symlink" in result.stdout
        assert files(binary.directory) == before


def test_no_entry_is_installed_when_mcp_registration_fails(
    binary: Binary, client: tuple[Path, dict[str, str]]
) -> None:
    result = setup(binary, (client[0], {**client[1], "PATH": ""}), "--write")
    assert result.returncode == 1
    assert not (client[0] / "skills/neosian-upkeep").exists()
    assert not (client[0] / "neosian-skills.json").exists()
