"""`setup --refresh` brings existing registrations to this release and no further."""

from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest

from tests.contract.refresh_support import (
    Machine,
    actions,
    by_client,
    machine,
    outdated_adapter,
    outdated_hooks,
    tree,
)
from tests.contract.support import ROOT, Binary, package_version

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")
PLUGIN = "neosian-cache@neosian-local"
SKILL = ROOT / "assets/clients/codex/skills/neosian-upkeep"
TEMPLATE = ROOT / "assets/clients/opencode-record.js"


@pytest.fixture
def host(binary: Binary) -> Machine:
    return machine(binary)


def test_current_registrations_stay_byte_identical(host: Machine) -> None:
    host.setup()
    before = tree(host.root)
    calls = {client: host.calls(client) for client in ("claude-code", "codex")}
    report = host.refresh()
    assert tree(host.root) == before
    assert {c: host.calls(c) for c in calls} == calls  # No client CLI ran.
    assert report["success"] is True
    assert report["version"] == package_version()
    assert report["level"] == "user"
    assert {c["client"]: c["result"] for c in report["clients"]} == {
        "claude-code": "current",
        "codex": "current",
        "opencode": "current",
        "pi": "current",
    }
    assert actions(by_client(report)["claude-code"]) == {
        "mcp": "unchanged",
        "hooks": "unchanged",
        "cache": "unchanged",
    }
    assert actions(by_client(report)["codex"])["skills"] == "unchanged"
    assert report["project"] == []
    assert report["restart"] == ["Claude Code", "Codex", "OpenCode", "Pi"]


def test_outdated_managed_entries_are_repaired_with_the_same_store(
    host: Machine,
) -> None:
    store = host.root / "chosen store"
    host.setup("--root", str(store))
    claude, codex = host.home("claude-code"), host.home("codex")
    registered = host.json(claude / ".claude.json")["mcpServers"]["neosian-memory"]
    outdated_hooks(claude / "settings.json", "SessionStart")
    display = claude / "neosian-cache/plugin/hooks/display.js"
    display.write_text("// an earlier plugin\n")
    outdated_adapter(host.home("opencode") / "plugins/neosian-record.js")
    pi = host.home("pi") / "mcp.json"
    document = host.json(pi)
    del document["mcpServers"]["neosian-memory"]["exposure"]
    pi.write_text(json.dumps(document))
    receipt = codex / "neosian-skills.json"
    saved = host.json(receipt)
    old = b"an earlier skill revision\n"
    saved["files"]["SKILL.md"] = hashlib.sha256(old).hexdigest()
    receipt.write_text(json.dumps(saved))
    (codex / "skills/neosian-upkeep/SKILL.md").write_bytes(old)
    assert host.run("setup", "--check", "--json").returncode == 1
    claude_calls = host.calls("claude-code")

    report = host.refresh()
    clients = by_client(report)
    assert {c: clients[c]["result"] for c in clients} == dict.fromkeys(
        ("claude-code", "codex", "opencode", "pi"), "updated"
    )
    assert actions(clients["claude-code"]) == {
        "mcp": "unchanged",
        "hooks": "update",
        "cache": "update",
    }
    assert actions(clients["codex"]) == {
        "mcp": "unchanged",
        "hooks": "unchanged",
        "skills": "update",
    }
    assert actions(clients["opencode"]) == {"mcp": "unchanged", "hooks": "update"}
    assert actions(clients["pi"]) == {"mcp": "update", "hooks": "unchanged"}
    # The registered command line, store included, is kept word for word.
    assert host.json(claude / ".claude.json")["mcpServers"]["neosian-memory"] == (
        registered
    )
    assert str(store) in registered["args"]
    hooks = host.json(claude / "settings.json")["hooks"]
    assert hooks["SessionStart"] == hooks["Stop"]
    assert str(store) in hooks["Stop"][0]["hooks"][0]["command"]
    assert (
        display.read_bytes()
        == (ROOT / "assets/clients/claude-cache/hooks/display.js").read_bytes()
    )
    assert host.calls("claude-code")[len(claude_calls) :] == [
        [
            "plugin",
            "marketplace",
            "add",
            str(claude / "neosian-cache"),
            "--scope",
            "user",
        ],
        ["plugin", "install", PLUGIN, "--scope", "user"],
    ]
    plugin = (host.home("opencode") / "plugins/neosian-record.js").read_text()
    argv = json.loads(plugin.split("const ARGV = ", 1)[1].split(";", 1)[0])
    assert str(store) in argv
    assert plugin == TEMPLATE.read_text().replace(
        "__NEOSIAN_RECORD_ARGV__", json.dumps(argv)
    )
    assert host.json(pi)["mcpServers"]["neosian-memory"]["exposure"] == "direct"
    assert (codex / "skills/neosian-upkeep/SKILL.md").read_bytes() == (
        SKILL / "SKILL.md"
    ).read_bytes()
    check = host.run("setup", "--check", "--json")
    assert check.returncode == 0, check.stdout
    assert not store.exists()
    before = tree(host.root)
    assert all(c["result"] == "current" for c in host.refresh()["clients"])
    assert tree(host.root) == before


def test_persisted_opt_outs_are_kept(host: Machine) -> None:
    host.setup("--codex-skills", "off", "--claude-cache", "off")
    claude, codex = host.home("claude-code"), host.home("codex")
    outdated_hooks(claude / "settings.json", "SessionStart")
    outdated_hooks(codex / "hooks.json", "SessionStart")
    statusline = host.json(claude / "settings.json").get("statusLine")
    report = host.refresh()
    clients = by_client(report)
    assert actions(clients["claude-code"])["cache"] == "off"
    assert actions(clients["codex"])["skills"] == "off"
    assert actions(clients["codex"])["hooks"] == "update"
    settings = host.json(claude / "settings.json")
    assert settings["enabledPlugins"][PLUGIN] is False
    assert settings.get("statusLine") == statusline
    assert not (codex / "skills").exists()
    assert host.json(codex / "neosian-skills.json")["enabled"] is False
    assert not any(call[0] == "plugin" for call in host.calls("claude-code"))


def test_one_client_failing_keeps_its_registration_and_others_proceed(
    host: Machine,
) -> None:
    host.setup()
    claude, codex = host.home("claude-code"), host.home("codex")
    # Claude Code fails after its first write: the plugin CLI refuses.
    outdated_hooks(claude / "settings.json", "SessionStart")
    (claude / "neosian-cache/plugin/hooks/display.js").write_text("// earlier\n")
    # Codex fails before any write: one hook event is not an array.
    hooks = host.json(codex / "hooks.json")
    hooks["hooks"]["PostToolUse"] = "kept by hand"
    (codex / "hooks.json").write_text(json.dumps(hooks))
    (codex / "skills/neosian-upkeep/SKILL.md").unlink()
    outdated_adapter(host.home("opencode") / "plugins/neosian-record.js")
    kept = {c: tree(host.home(c)) for c in ("claude-code", "codex")}

    report = host.refresh(code=1, CLAUDE_FAIL="plugin install")
    clients = by_client(report)
    assert report["success"] is False
    assert clients["claude-code"]["result"] == "failed"
    assert "claude double refused plugin install" in clients["claude-code"]["error"]
    assert actions(clients["claude-code"])["hooks"] == "update"  # Not written.
    assert clients["codex"]["result"] == "failed"
    assert "PostToolUse" in clients["codex"]["error"]
    assert clients["codex"]["hint"]
    assert clients["codex"]["parts"] == []
    assert clients["opencode"]["result"] == "updated"
    assert clients["pi"]["result"] == "current"
    for client, files in kept.items():
        now = tree(host.home(client))
        now.pop("commands.jsonl", None)
        files.pop("commands.jsonl", None)
        assert now == files, client
    assert "Claude Code" in report["restart"]

    text = host.run("setup", "--refresh", CLAUDE_FAIL="plugin install")
    assert text.returncode == 1
    assert "Claude Code: failed; nothing was changed" in text.stdout
    assert "  hooks   " in text.stdout and "not updated" in text.stdout
    assert "retry   neosian setup --refresh once the cause is fixed" in text.stdout
    assert text.stdout.endswith(f"to load neosian {package_version()}.\n")


def test_project_registrations_are_listed_and_never_edited(host: Machine) -> None:
    host.setup()
    executable = str(host.binary.executable)
    mcp = {"mcpServers": {"neosian-memory": {"command": executable, "args": ["mcp"]}}}
    record = f"{executable} record --root ./state"
    group = {"hooks": [{"type": "command", "command": record}]}
    shared = {
        ".mcp.json": json.dumps(mcp),
        ".claude/settings.json": json.dumps({"hooks": {"Stop": [group]}}),
        ".codex/hooks.json": json.dumps({"hooks": {"Stop": [group]}}),
        "opencode.json": json.dumps(
            {"mcp": {"neosian-memory": {"type": "local", "command": [executable]}}}
        ),
    }
    for name, text in shared.items():
        (host.project / name).parent.mkdir(exist_ok=True)
        (host.project / name).write_text(text)
    outdated_hooks(host.home("claude-code") / "settings.json", "SessionStart")
    before = tree(host.project)
    report = host.refresh()
    assert tree(host.project) == before
    assert actions(by_client(report)["claude-code"])["hooks"] == "update"
    listed = {(p["client"], p["kind"], p["path"]) for p in report["project"]}
    project = str(host.project.resolve())
    assert {
        ("claude-code", "mcp", f"{project}/.mcp.json"),
        ("claude-code", "hooks", f"{project}/.claude/settings.json"),
        ("codex", "hooks", f"{project}/.codex/hooks.json"),
        ("opencode", "mcp", f"{project}/opencode.json"),
        ("muse-code", "mcp", f"{project}/.mcp.json"),
    } == {(c, k, os.path.realpath(p)) for c, k, p in listed}
    text = host.run("setup", "--refresh").stdout
    assert "Project files are listed here and never edited:" in text
    assert tree(host.project) == before


def test_foreign_and_unrecognized_registrations_are_left_alone(
    host: Machine,
) -> None:
    host.setup()
    other = host.root / "other installation" / "neosian"
    other.parent.mkdir()
    shutil.copy2(host.binary.executable, other)
    pi = host.home("pi") / "mcp.json"
    document = host.json(pi)
    entry = document["mcpServers"]["neosian-memory"]
    entry["command"] = str(other)
    del entry["exposure"]
    pi.write_text(json.dumps(document))
    hooks = host.home("codex") / "hooks.json"
    hooks.write_text(hooks.read_text().replace(" record ", " record --verbose yes "))
    outdated_adapter(host.home("opencode") / "plugins/neosian-record.js")
    plugin = host.home("opencode") / "plugins/neosian-record.js"
    plugin.write_text(
        plugin.read_text().replace('"record", ', '"record", "--tail", "1", ')
    )
    before = tree(host.root)
    report = host.refresh()
    assert tree(host.root) == before
    clients = by_client(report)
    reasons = {
        client: {p["kind"]: p["reason"] for p in clients[client]["parts"]}
        for client in ("pi", "codex", "opencode")
    }
    assert reasons["pi"]["mcp"].startswith(
        "the registered executable is another installation"
    )
    assert reasons["codex"]["hooks"] == (
        "--verbose is not written that way by this release"
    )
    assert reasons["opencode"]["hooks"] == (
        "--tail is not written that way by this release"
    )
    assert clients["pi"]["result"] == "left_alone"
    assert report["success"] is True


def test_nothing_new_is_connected(host: Machine) -> None:
    for client in ("claude-code", "codex"):
        shutil.rmtree(host.home(client))
    host.home("opencode").rmdir()
    assert host.run("setup", "--client=pi", "--write").returncode == 0
    (host.home("codex")).mkdir()
    before = tree(host.root)
    report = host.refresh()
    assert [c["client"] for c in report["clients"]] == ["pi"]
    assert tree(host.root) == before
    assert not host.home("opencode").exists()
    assert not host.home("claude-code").exists()
    assert list(host.home("codex").iterdir()) == []
    shutil.rmtree(host.home("pi"))
    empty = host.run("setup", "--refresh")
    assert empty.returncode == 0
    assert "No user-level registration found; nothing changed." in empty.stdout
    assert "Restart" not in empty.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ["--write"],
        ["--check"],
        ["--client", "pi"],
        ["--root", "elsewhere"],
        ["--level", "user"],
        ["--codex-skills", "on"],
    ],
)
def test_refresh_selects_nothing_new(host: Machine, extra: list[str]) -> None:
    before = tree(host.root)
    result = host.run("setup", "--refresh", *extra, "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "usage"
    assert tree(host.root) == before
