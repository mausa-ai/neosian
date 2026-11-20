"""The refresh runs unasked, so what a person or a client changed is kept."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest

from tests.contract.refresh_support import (
    Machine,
    actions,
    by_client,
    machine,
    outdated_hooks,
    tree,
)
from tests.contract.support import ROOT, Binary

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")
DISPLAY = "neosian-cache/plugin/hooks/display.js"


@pytest.fixture
def host(binary: Binary) -> Machine:
    return machine(binary)


def hook_file(host: Machine, client: str) -> Path:
    name = {"codex": "hooks.json", "cursor": "hooks.json"}.get(client, "settings.json")
    return host.home(client) / name


def ours(document: Any, event: str) -> str:
    group = document["hooks"][event][0]
    return str(group["command"] if "command" in group else group["hooks"][0]["command"])


def converged(host: Machine) -> None:
    before = tree(host.root)
    report = host.refresh()
    assert {c["result"] for c in report["clients"]} <= {"current", "left_alone"}
    assert report["restart"] == []
    assert tree(host.root) == before


def test_hook_groups_shared_or_extended_by_hand_are_kept(host: Machine) -> None:
    host.setup()
    expected = {}
    for client in ("claude-code", "codex", "muse-code"):
        path = hook_file(host, client)
        document = host.json(path)
        command = ours(document, "Stop")
        hooks = document["hooks"]
        hooks["Stop"][0]["hooks"].append({"type": "command", "command": "echo mine"})
        hooks["UserPromptSubmit"][0]["hooks"][0]["timeout"] = 30
        hooks["PostToolUse"][0]["matcher"] = "Bash"
        hooks["PreToolUse"] = [{"hooks": [{"type": "command", "command": "echo pre"}]}]
        del hooks["SessionStart"]
        path.write_text(json.dumps(document, indent=2))
        wanted = copy.deepcopy(document)
        fresh = {"hooks": [{"type": "command", "command": command}]}
        wanted["hooks"]["SessionStart"] = [fresh]
        expected[client] = (path, wanted)
    path = hook_file(host, "cursor")
    document = host.json(path)
    command = ours(document, "stop")
    document["hooks"]["stop"][0]["timeout"] = 30
    document["hooks"]["stop"].insert(0, {"command": "echo mine"})
    del document["hooks"]["sessionStart"]
    path.write_text(json.dumps(document))
    wanted = copy.deepcopy(document)
    wanted["hooks"]["sessionStart"] = [{"command": command}]
    expected["cursor"] = (path, wanted)

    report = host.refresh()
    for client, (path, wanted) in expected.items():
        assert actions(by_client(report)[client])["hooks"] == "update", client
        assert host.json(path) == wanted, client
        # Existing groups keep their position; the fresh one is appended.
        assert list(host.json(path)["hooks"]) == list(wanted["hooks"]), client
    converged(host)


def test_an_event_the_release_dropped_loses_only_our_hook(host: Machine) -> None:
    host.setup()
    path = hook_file(host, "claude-code")
    document = host.json(path)
    group = document["hooks"]["Stop"][0]
    document["hooks"]["Notification"] = [copy.deepcopy(group)]
    shared = copy.deepcopy(group)
    shared["hooks"].append({"type": "command", "command": "echo mine"})
    document["hooks"]["SubagentStop"] = [shared]
    path.write_text(json.dumps(document))
    report = host.refresh()
    assert actions(by_client(report)["claude-code"])["hooks"] == "update"
    hooks = host.json(path)["hooks"]
    assert "Notification" not in hooks
    assert hooks["SubagentStop"] == [
        {"hooks": [{"type": "command", "command": "echo mine"}]}
    ]
    converged(host)


def test_links_modes_and_read_only_files_are_respected(host: Machine) -> None:
    host.setup()
    claude = host.home("claude-code")
    dotfiles = host.root / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "claude-settings.json"
    shutil.move(claude / "settings.json", real)
    (claude / "settings.json").symlink_to(real)
    outdated_hooks(real, "SessionStart")
    pi = host.home("pi") / "mcp.json"
    document = host.json(pi)
    del document["mcpServers"]["neosian-memory"]["exposure"]
    pi.write_text(json.dumps(document))
    pi.chmod(0o640)
    codex = host.home("codex") / "hooks.json"
    outdated_hooks(codex, "SessionStart")
    codex.chmod(0o444)
    kept = codex.read_bytes()

    report = host.refresh()
    clients = by_client(report)
    assert os.readlink(claude / "settings.json") == str(real)
    assert "SessionStart" in host.json(real)["hooks"]
    assert actions(clients["claude-code"])["hooks"] == "update"
    assert stat.S_IMODE(pi.stat().st_mode) == 0o640
    assert host.json(pi)["mcpServers"]["neosian-memory"]["exposure"] == "direct"
    assert codex.read_bytes() == kept and stat.S_IMODE(codex.stat().st_mode) == 0o444
    part = next(p for p in clients["codex"]["parts"] if p["kind"] == "hooks")
    assert part["action"] == "left_alone" and "read-only" in part["reason"]
    assert clients["codex"]["result"] == "left_alone"
    assert report["success"] is True


def test_a_failed_write_restores_only_what_this_run_wrote(host: Machine) -> None:
    host.setup()
    claude, codex = host.home("claude-code"), host.home("codex")
    dotfiles = host.root / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "claude-settings.json"
    shutil.move(claude / "settings.json", real)
    (claude / "settings.json").symlink_to(real)
    # Claude Code: its hooks are written through the link, then the plugin CLI fails.
    outdated_hooks(real, "SessionStart")
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    # Codex: the skill files are created, then its home refuses the receipt.
    shutil.rmtree(codex / "skills/neosian-upkeep")
    before = {
        "claude": tree(claude),
        "dotfiles": tree(dotfiles),
        "codex": tree(codex),
    }
    codex.chmod(0o555)
    try:
        report = host.refresh(code=1, CLAUDE_FAIL="plugin install")
    finally:
        codex.chmod(0o755)
    clients = by_client(report)
    assert clients["claude-code"]["result"] == "failed"
    assert clients["codex"]["result"] == "failed"
    # The cause names the write that failed; no untouched file is named.
    error = clients["codex"]["error"]
    assert error.startswith("cannot write ") and "Permission denied" in error
    assert "restored" not in error and "meanwhile" not in error
    after = tree(claude)
    after.pop("commands.jsonl")
    before["claude"].pop("commands.jsonl")
    assert after == before["claude"]
    assert tree(dotfiles) == before["dotfiles"]
    assert tree(codex) == before["codex"]


def test_restore_leaves_a_file_its_client_changed_meanwhile(host: Machine) -> None:
    host.setup()
    claude = host.home("claude-code")
    outdated_hooks(claude / "settings.json", "SessionStart")
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    report = host.refresh(code=1, CLAUDE_FAIL="plugin install", CLAUDE_TOUCH="1")
    client = by_client(report)["claude-code"]
    assert client["result"] == "failed"
    assert "changed by its client meanwhile" in client["error"]
    settings = host.json(claude / "settings.json")
    assert settings["touchedByClaude"] is True
    assert "SessionStart" in settings["hooks"]
    assert (claude / DISPLAY).read_text() == "// an earlier plugin\n"


@pytest.mark.parametrize("enabled", [None, False])
def test_values_a_person_changed_are_kept(host: Machine, enabled: bool | None) -> None:
    host.setup()
    pi = host.home("pi") / "mcp.json"
    document = host.json(pi)
    document["mcpServers"]["neosian-memory"]["exposure"] = "proxy"
    pi.write_text(json.dumps(document))
    opencode = host.home("opencode") / "opencode.json"
    document = host.json(opencode)
    entry = document["mcp"]["neosian-memory"]
    if enabled is None:
        del entry["enabled"]
    else:
        entry["enabled"] = enabled
    opencode.write_text(json.dumps(document))
    claude = host.home("claude-code")
    settings = host.json(claude / "settings.json")
    settings["statusLine"]["padding"] = 3
    settings["statusLine"]["refreshInterval"] = 7
    (claude / "settings.json").write_text(json.dumps(settings))
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    kept = {path: path.read_bytes() for path in (pi, opencode)}

    report = host.refresh()
    clients = by_client(report)
    assert actions(clients["pi"])["mcp"] == "unchanged"
    assert actions(clients["opencode"])["mcp"] == "unchanged"
    assert {path: path.read_bytes() for path in kept} == kept
    assert actions(clients["claude-code"])["cache"] == "update"
    line = host.json(claude / "settings.json")["statusLine"]
    assert (line["padding"], line["refreshInterval"]) == (3, 7)
    assert line["command"] == settings["statusLine"]["command"]
    converged(host)


def test_an_interrupted_refresh_is_completed_by_running_it_again(
    host: Machine,
) -> None:
    host.setup()
    claude, codex = host.home("claude-code"), host.home("codex")
    # As a refresh stopped midway leaves it: some files new, others still old.
    for name in ("hooks/display.js", "hooks/usage.js"):
        (claude / "neosian-cache/plugin" / name).write_text("// earlier\n")
    receipt = codex / "neosian-skills.json"
    saved = host.json(receipt)
    old = b"an earlier skill revision\n"
    for name in ("SKILL.md", "agents/openai.yaml"):
        saved["files"][name] = hashlib.sha256(old).hexdigest()
    receipt.write_text(json.dumps(saved))
    (codex / "skills/neosian-upkeep/agents/openai.yaml").write_bytes(old)
    outdated_hooks(hook_file(host, "muse-code"), "SessionStart")
    report = host.refresh()
    clients = by_client(report)
    assert actions(clients["claude-code"])["cache"] == "update"
    assert actions(clients["codex"])["skills"] == "update"
    assert actions(clients["muse-code"])["hooks"] == "update"
    assert (claude / DISPLAY).read_bytes() == (
        ROOT / "assets/clients/claude-cache/hooks/display.js"
    ).read_bytes()
    assert host.run("setup", "--check", "--json").returncode == 0
    converged(host)
