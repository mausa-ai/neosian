"""A part the refresh cannot change faithfully is left alone, with its reason;
the client's other parts proceed and the refresh still succeeds."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tomllib
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
from tests.contract.support import Binary

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")
DISPLAY = "neosian-cache/plugin/hooks/display.js"


@pytest.fixture
def host(binary: Binary) -> Machine:
    return machine(binary)


def reason(report: Any, client: str, kind: str) -> str:
    part = next(p for p in by_client(report)[client]["parts"] if p["kind"] == kind)
    assert part["action"] == "left_alone", part
    return str(part["reason"])


def test_every_unfaithful_registration_is_left_alone_with_its_reason(
    host: Machine,
) -> None:
    host.setup()
    # More than one store selected.
    pi = host.home("pi") / "mcp.json"
    document = host.json(pi)
    document["mcpServers"]["neosian"]["args"] += ["--url", "http://h"]
    pi.write_text(json.dumps(document))
    # A flag written twice.
    codex = host.home("codex") / "hooks.json"
    codex.write_text(codex.read_text().replace(" record ", " record --agent x "))
    # Commands that differ between events.
    cursor = host.home("cursor") / "hooks.json"
    document = host.json(cursor)
    document["hooks"]["stop"][0]["command"] += " --spool /elsewhere"
    cursor.write_text(json.dumps(document))
    # Our hooks in more than one file.
    muse = host.home("muse-code") / "settings.json"
    document = host.json(muse)
    document["managed_hooks_path"] = "other-hooks.json"
    muse.write_text(json.dumps(document))
    (host.home("muse-code") / "other-hooks.json").write_text(
        json.dumps({"hooks": document["hooks"]})
    )
    # A command that cannot be read back word for word.
    opencode = host.home("opencode") / "opencode.json"
    document = host.json(opencode)
    document["mcp"]["neosian"]["command"] += ["--mount", 5]
    opencode.write_text(json.dumps(document))
    # A statusline that no longer carries the observation bridge.
    claude = host.home("claude-code")
    settings = host.json(claude / "settings.json")
    settings["statusLine"] = {"type": "command", "command": "echo mine"}
    (claude / "settings.json").write_text(json.dumps(settings))
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    before = tree(host.root)

    report = host.refresh()
    assert tree(host.root) == before
    assert report["success"] is True
    assert reason(report, "pi", "mcp") == "more than one store is selected"
    assert reason(report, "codex", "hooks") == (
        "--agent is not written that way by this release"
    )
    assert (
        reason(report, "cursor", "hooks") == "the hook commands differ between events"
    )
    assert reason(report, "muse-code", "hooks").startswith(
        "the hooks are present in more than one file"
    )
    assert reason(report, "opencode", "mcp") == (
        "the registered command could not be read back word for word"
    )
    assert reason(report, "claude-code", "cache").startswith(
        "the statusline no longer carries the observation bridge"
    )
    assert report["restart"] == []


def test_skill_and_plugin_follow_the_installation_their_client_runs(
    host: Machine,
) -> None:
    host.setup()
    other = host.root / "other installation" / "neosian"
    other.parent.mkdir()
    shutil.copy2(host.binary.executable, other)
    mine = str(host.binary.executable)
    codex = host.home("codex")
    for name in ("config.toml", "hooks.json"):
        path = codex / name
        path.write_text(path.read_text().replace(mine, str(other)))
    assert tomllib.loads((codex / "config.toml").read_text())
    receipt = codex / "neosian-skills.json"
    saved = host.json(receipt)
    old = b"an earlier skill revision\n"
    saved["files"]["SKILL.md"] = hashlib.sha256(old).hexdigest()
    receipt.write_text(json.dumps(saved))
    (codex / "skills/neosian-upkeep/SKILL.md").write_bytes(old)
    claude = host.home("claude-code")
    for path in (claude / ".claude.json", claude / "settings.json"):
        path.write_text(path.read_text().replace(mine, str(other)))
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    before = tree(host.root)

    report = host.refresh()
    assert tree(host.root) == before
    for client, kind in (("codex", "skills"), ("claude-code", "cache")):
        assert "another installation" in reason(report, client, kind)
        assert by_client(report)[client]["result"] == "left_alone"


def test_conditions_found_before_writing_leave_one_part_alone(host: Machine) -> None:
    host.setup()
    codex, claude = host.home("codex"), host.home("claude-code")
    edited = codex / "skills/neosian-upkeep/SKILL.md"
    edited.write_text("my own notes in the managed skill\n")
    outdated_hooks(codex / "hooks.json", "SessionStart")
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    outdated_hooks(claude / "settings.json", "SessionStart")
    empty = host.root / "no commands"
    empty.mkdir()

    report = host.refresh(PATH=str(empty))
    assert report["success"] is True
    clients = by_client(report)
    assert "local edits" in reason(report, "codex", "skills")
    assert actions(clients["codex"])["hooks"] == "update"
    assert clients["codex"]["result"] == "updated"
    assert "claude" in reason(report, "claude-code", "cache")
    assert actions(clients["claude-code"])["hooks"] == "update"
    assert "SessionStart" in host.json(claude / "settings.json")["hooks"]
    assert edited.read_text() == "my own notes in the managed skill\n"
    assert (claude / DISPLAY).read_text() == "// an earlier plugin\n"
    assert report["restart"] == ["Claude Code", "Codex"]


def test_a_plugin_claude_no_longer_lists_is_not_reinstalled(host: Machine) -> None:
    host.setup()
    claude = host.home("claude-code")
    settings = host.json(claude / "settings.json")
    del settings["enabledPlugins"]
    (claude / "settings.json").write_text(json.dumps(settings))
    (claude / DISPLAY).write_text("// an earlier plugin\n")
    calls = host.calls("claude-code")
    before = tree(host.root)
    report = host.refresh()
    assert "cache" not in actions(by_client(report)["claude-code"])
    assert host.calls("claude-code") == calls
    assert tree(host.root) == before


def test_the_bridge_path_is_compared_canonically(host: Machine) -> None:
    linked = host.root / "linked clients"
    linked.symlink_to(host.root / "clients")
    host.run(
        "setup",
        "--client=claude-code",
        "--write",
        CLAUDE_CONFIG_DIR=str(linked / "claude-code"),
    )
    before = tree(host.root)
    report = host.refresh()
    assert actions(by_client(report)["claude-code"])["cache"] == "unchanged"
    assert tree(host.root) == before


def test_a_directory_its_owner_cannot_write_leaves_the_part_alone(
    host: Machine,
) -> None:
    host.setup()
    codex, claude = host.home("codex"), host.home("claude-code")
    (codex / "skills/neosian-upkeep/SKILL.md").unlink()
    plugin = claude / "neosian-cache/plugin/hooks"
    (plugin / "display.js").write_text("// an earlier plugin\n")
    before = tree(host.root)
    modes = {path: path.stat().st_mode for path in (codex, plugin)}
    for path in modes:
        path.chmod(0o555)
    try:
        report = host.refresh()
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
    assert tree(host.root) == before
    assert report["success"] is True
    assert reason(report, "codex", "skills").startswith(f"{codex} is not writable")
    assert reason(report, "claude-code", "cache").startswith(
        f"{plugin} is not writable"
    )


def test_a_dangling_symlink_is_named_as_one(host: Machine) -> None:
    host.setup()
    claude = host.home("claude-code")
    (claude / DISPLAY).unlink()
    (claude / DISPLAY).symlink_to(host.root / "gone" / "display.js")
    report = host.refresh()
    assert reason(report, "claude-code", "cache") == (
        f"{claude / DISPLAY} is a symbolic link whose target does not exist; "
        "it is left as it is"
    )
    plugin = host.home("opencode") / "plugins/neosian-record.js"
    plugin.unlink()
    plugin.symlink_to(host.root / "gone" / "neosian-record.js")
    result = host.run("setup", "--client=opencode", "--write", "--json")
    assert result.returncode == 1
    hooks = json.loads(result.stdout)["clients"][0]["hooks"]
    assert hooks["error"] == (
        f"cannot write {plugin}: it is a symbolic link whose target does not exist"
    )
    assert plugin.is_symlink() and not plugin.exists()
