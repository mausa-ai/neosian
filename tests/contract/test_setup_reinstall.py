"""The rename as a reinstall (DESIGN §40.53, rules N4 to N9): every client
on 2.20.0's `neosian-memory` moved to `neosian` by the refresh when nothing
a person wrote names neosian, left working otherwise with the command that
moves it, refused beside another server's `neosian`, whole or not at all,
put back by one command, silent on a second run, and never printing a value."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.refresh_support import (
    FORMER,
    RECEIPT,
    SERVER,
    Machine,
    by_client,
    claude_json,
    claude_project,
    machine,
    migrations,
    renamed,
    tree,
)
from tests.contract.support import Binary

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")
SECRET = "literal-value-never-printed"
FILES = {
    "claude-code": (".claude.json", "mcpServers"),
    "opencode": ("opencode.json", "mcp"),
    "muse-code": ("settings.json", "mcpServers"),
    "pi": ("mcp.json", "mcpServers"),
}


def desktop(host: Machine) -> Path:
    base = (
        "Library/Application Support/Claude"
        if sys.platform == "darwin"
        else ".config/Claude"
    )
    return host.root / base / "claude_desktop_config.json"


def registration_file(host: Machine, client: str) -> tuple[Path, str]:
    if client == "cursor":
        return host.root / ".cursor/mcp.json", "mcpServers"
    if client == "claude-desktop":
        return desktop(host), "mcpServers"
    name, key = FILES[client]
    return host.home(client) / name, key


def keys(host: Machine, client: str) -> list[str]:
    if client == "codex":
        text = (host.home("codex") / "config.toml").read_text()
        return [k for k in (FORMER, SERVER) if f"[mcp_servers.{k}]" in text]
    path, key = registration_file(host, client)
    return list(json.loads(path.read_text())[key])


def released(host: Machine) -> None:
    """Every client as 2.20.0 left it: our entries under `neosian-memory`,
    no receipts; Claude Desktop registered too."""
    for client in ("claude-code", "opencode", "muse-code", "pi", "cursor"):
        path, key = registration_file(host, client)
        document = json.loads(path.read_text())
        document[key] = renamed(document[key], SERVER, FORMER)
        path.write_text(json.dumps(document, indent=1))
    config = host.home("codex") / "config.toml"
    config.write_text(
        config.read_text().replace(f"[mcp_servers.{SERVER}]", f"[mcp_servers.{FORMER}]")
    )
    entry = {
        "command": str(host.binary.executable),
        "args": ["mcp"],
        "env": {"TOKEN": SECRET},
    }
    desktop(host).parent.mkdir(parents=True, exist_ok=True)
    desktop(host).write_text(json.dumps({"mcpServers": {FORMER: entry}}))
    for client in ("claude-code", "codex", "opencode", "muse-code", "pi", "cursor"):
        (host.home(client) / RECEIPT).unlink(missing_ok=True)
    (host.root / ".cursor" / RECEIPT).unlink(missing_ok=True)


@pytest.fixture
def host(binary: Binary) -> Machine:
    machine_ = machine(binary)
    machine_.setup()
    released(machine_)
    return machine_


CLIENTS = (
    "claude-code",
    "codex",
    "opencode",
    "muse-code",
    "cursor",
    "pi",
    "claude-desktop",
)


def settings(host: Machine) -> Path:
    return host.home("claude-code") / "settings.json"


def edit(path: Path, change: Any) -> None:
    document = json.loads(path.read_text()) if path.exists() else {}
    change(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1))


def rename_member(report: Any, client: str) -> Any:
    return by_client(report)[client]["rename"]


# N4, N7, N9: a move by the refresh with nothing blocking.


def test_the_refresh_moves_every_client_when_nothing_blocks(host: Machine) -> None:
    before = {c: keys(host, c) for c in CLIENTS}
    assert all(k == [FORMER] for k in before.values()), before
    entry = json.loads(desktop(host).read_text())["mcpServers"][FORMER]
    report = host.refresh()
    assert {c: keys(host, c) for c in CLIENTS} == {c: [SERVER] for c in CLIENTS}
    assert all(rename_member(report, c)["state"] == "moved" for c in CLIENTS)
    assert json.loads(desktop(host).read_text())["mcpServers"][SERVER] == entry
    receipt = json.loads((host.home("claude-code") / RECEIPT).read_text())
    assert [e["pointer"] for e in receipt["entries"]] == ["/mcpServers/neosian"]
    [run] = migrations(host)
    assert stat.S_IMODE(run.stat().st_mode) == 0o700
    copies = list((run / "files").iterdir())
    assert copies and all(stat.S_IMODE(c.stat().st_mode) == 0o600 for c in copies)
    text = host.run("status").stdout
    assert "on neosian" in text and "on neosian-memory" not in text


def test_a_second_run_changes_nothing(host: Machine) -> None:
    host.refresh()
    before = tree(host.root)
    report = host.refresh()
    rows = by_client(report)
    assert all(rows[c]["rename"]["state"] == "unchanged" for c in CLIENTS if c in rows)
    assert tree(host.root) == before
    assert len(migrations(host)) == 1


# N5, N7: allow entries do not block; they are listed.


def test_allow_entries_naming_old_tools_move_and_are_listed(host: Machine) -> None:
    edit(
        settings(host),
        lambda d: d.update(permissions={"allow": ["mcp__neosian-memory__memory"]}),
    )
    cli = host.home("cursor") / "cli-config.json"
    edit(cli, lambda d: d.update(permissions={"allow": ["Mcp(neosian-memory:*)"]}))
    report = host.refresh()
    assert keys(host, "claude-code") == [SERVER] and keys(host, "cursor") == [SERVER]
    allows = rename_member(report, "claude-code")["allows"]
    assert [Path(a["path"]).name for a in allows] == ["settings.json"] and allows[0][
        "line"
    ] > 1
    assert json.loads(settings(host).read_text())["permissions"]["allow"] == [
        "mcp__neosian-memory__memory"
    ]
    assert rename_member(report, "cursor")["allows"]


def test_the_allow_line_says_the_client_will_ask_again(host: Machine) -> None:
    edit(
        settings(host),
        lambda d: d.update(permissions={"allow": ["mcp__neosian-memory"]}),
    )
    text = host.run("setup", "--refresh").stdout
    assert "Claude Code moved to neosian." in text
    assert (
        "allows the old tool names; the client will ask again; rename the entry to keep it"
        in text
    )


# N4, N6: a person's rule naming the old tools keeps the client until the command.


def agent(host: Machine) -> None:
    path = host.home("claude-code") / "agents" / "guard.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "---\nname: guard\ndescription: d\ndisallowedTools: mcp__neosian-memory__memory\n---\nbody\n"
    )


BLOCKERS = {
    "deny": lambda host: edit(
        settings(host),
        lambda d: d.update(permissions={"deny": ["mcp__neosian-memory__harness"]}),
    ),
    "matcher": lambda host: edit(
        settings(host),
        lambda d: d["hooks"].update(
            PreToolUse=[
                {
                    "matcher": "mcp__neosian-memory__memory",
                    "hooks": [{"type": "command", "command": "exit 2"}],
                }
            ]
        ),
    ),
    "agent": agent,
}


@pytest.mark.parametrize("kind", BLOCKERS)
def test_a_rule_naming_old_tools_keeps_the_client_until_the_command(
    host: Machine, kind: str
) -> None:
    BLOCKERS[kind](host)
    before = settings(host).read_text()
    report = host.refresh()
    member = rename_member(report, "claude-code")
    assert member["state"] == "left" and keys(host, "claude-code") == [FORMER]
    [mention] = member["mentions"]
    assert mention["line"] >= 1
    assert all(
        rename_member(report, c)["state"] == "moved"
        for c in CLIENTS
        if c not in ("claude-code", "muse-code")
    )
    text = host.run("setup", "--refresh").stdout
    assert "Claude Code stays on neosian-memory, which keeps working." in text
    assert f"{mention['path']}:{mention['line']} names neosian" in text
    assert (
        "To move it anyway (this list may be incomplete): neosian setup --refresh --rename"
        in text
    )
    moved = host.run("setup", "--refresh", "--rename", "--json")
    assert moved.returncode == 0, moved.stdout
    assert keys(host, "claude-code") == [SERVER]
    assert settings(host).read_text() == before


# N4, N6: another server named `neosian` is refused both ways.


def test_another_server_named_neosian_is_refused_both_ways(host: Machine) -> None:
    foreign = {"type": "http", "url": "https://other.example/mcp"}
    edit(claude_json(host), lambda d: d["mcpServers"].update({SERVER: foreign}))
    cursor = host.root / ".cursor/mcp.json"
    edit(cursor, lambda d: d["mcpServers"].update({SERVER: foreign}))
    for args in (
        ("setup", "--refresh", "--json"),
        ("setup", "--refresh", "--rename", "--json"),
    ):
        report = json.loads(host.run(*args).stdout)
        for client in ("claude-code", "cursor"):
            member = rename_member(report, client)
            assert member["state"] == "left" and [
                s["kind"] for s in member["stops"]
            ] == ["taken"]
    assert json.loads(claude_json(host).read_text())["mcpServers"][SERVER] == foreign
    assert json.loads(cursor.read_text())["mcpServers"][SERVER] == foreign
    assert keys(host, "claude-code") == [FORMER, SERVER] or keys(
        host, "claude-code"
    ) == [SERVER, FORMER]


def test_an_entry_under_the_old_key_with_a_url_is_listed_and_never_moved(
    host: Machine,
) -> None:
    hosted = {"type": "http", "url": "https://cloud.example/mcp"}
    edit(desktop(host), lambda d: d["mcpServers"].update({FORMER: hosted}))
    report = host.refresh()
    member = rename_member(report, "claude-desktop")
    assert [h["at"] for h in member["hosted"]] == ["/mcpServers/neosian-memory"]
    assert json.loads(desktop(host).read_text())["mcpServers"] == {FORMER: hosted}


# N8: a failure part-way puts the client back.


def test_a_failure_part_way_puts_the_client_back(host: Machine) -> None:
    project = claude_project(host, "work", {})
    entry = json.loads(claude_json(host).read_text())["mcpServers"][FORMER]
    edit(
        claude_json(host),
        lambda d: d["projects"][str(project.resolve())].update(
            mcpServers={FORMER: entry}
        ),
    )
    report = host.refresh(code=1, CLAUDE_FAIL="mcp add-json --scope local")
    client = by_client(report)["claude-code"]
    assert client["result"] == "failed" and "claude double refused" in client["error"]
    document = json.loads(claude_json(host).read_text())
    assert list(document["mcpServers"]) == [FORMER]
    assert list(document["projects"][str(project.resolve())]["mcpServers"]) == [FORMER]
    receipt = host.home("claude-code") / RECEIPT
    assert not receipt.exists() or json.loads(receipt.read_text())["entries"] == []
    assert (
        "it is back on neosian-memory"
        in host.run(
            "setup", "--refresh", CLAUDE_FAIL="mcp add-json --scope local"
        ).stdout
    )


# N9: going back, and refusing where a person replaced our entry.


def test_going_back_puts_clients_on_the_old_name_and_holds_them(host: Machine) -> None:
    host.refresh()
    replaced = {"command": "/opt/other", "args": []}
    edit(
        host.root / ".cursor/mcp.json",
        lambda d: d["mcpServers"].update({SERVER: replaced}),
    )
    result = host.run("setup", "--undo-rename", "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1 and report["success"] is False
    assert {c: keys(host, c) for c in CLIENTS if c != "cursor"} == {
        c: [FORMER] for c in CLIENTS if c != "cursor"
    }
    [cursor] = [r for r in report["clients"] if r["client"] == "cursor"]
    assert cursor["refused"] and keys(host, "cursor") == [SERVER]
    again = host.refresh()
    assert rename_member(again, "codex")["state"] == "left"
    assert keys(host, "codex") == [FORMER]
    assert host.run("setup", "--refresh", "--rename", "--json").returncode == 0
    assert keys(host, "codex") == [SERVER]


def test_going_back_one_client(host: Machine) -> None:
    host.refresh()
    result = host.run("setup", "--undo-rename", "codex")
    assert result.returncode == 0, result.stdout
    assert "Codex is back on neosian-memory." in result.stdout
    assert keys(host, "codex") == [FORMER] and keys(host, "pi") == [SERVER]


# Codex: the table moves with its own members.


def test_codex_moves_its_table_with_every_member(host: Machine) -> None:
    config = host.home("codex") / "config.toml"
    config.write_text(
        config.read_text()
        + f'\n[mcp_servers.{FORMER}.tools.harness]\napproval_mode = "approve"\n'
    )
    text = config.read_text()
    host.refresh()
    assert config.read_text() == text.replace(
        f"mcp_servers.{FORMER}", f"mcp_servers.{SERVER}"
    )


# The shared project `.mcp.json` moves only with every client reading it.


def test_a_shared_mcp_json_waits_for_every_client_that_reads_it(host: Machine) -> None:
    entry = {"command": str(host.binary.executable), "args": ["mcp"]}
    (host.project / ".mcp.json").write_text(json.dumps({"mcpServers": {FORMER: entry}}))
    agents = host.home("muse-code") / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "a.md").write_text(
        "---\nname: a\ndisallowedTools: mcp__neosian_memory__memory\n---\n"
    )
    report = host.refresh()
    assert rename_member(report, "muse-code")["state"] == "left"
    member = rename_member(report, "claude-code")
    assert member["state"] == "left" and [s["kind"] for s in member["stops"]] == [
        "shared"
    ]
    assert list(json.loads((host.project / ".mcp.json").read_text())["mcpServers"]) == [
        FORMER
    ]
    assert keys(host, "claude-code") == [FORMER]


# §40.53 addition: this directory's project files are always read.


def test_this_directorys_old_entry_beside_our_new_one_is_named_with_its_fix(
    host: Machine,
) -> None:
    host.refresh()
    entry = json.loads((host.root / ".cursor/mcp.json").read_text())["mcpServers"][
        SERVER
    ]
    project = host.project / ".cursor/mcp.json"
    project.parent.mkdir()
    project.write_text(json.dumps({"mcpServers": {FORMER: entry}}))
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "cursor"]
    [pair] = row["two_servers"]
    assert pair["project"] == str(host.project.resolve())
    check = host.run("setup", "--check", "--json")
    assert check.returncode == 1
    issue = next(
        i for i in json.loads(check.stdout)["issues"] if i.startswith("cursor")
    )
    assert f"neosian setup --refresh --rename in {host.project.resolve()}" in issue
    host.refresh()
    assert list(json.loads(project.read_text())["mcpServers"]) == [SERVER]


# N7: no value in any report.


def test_no_value_in_any_report(host: Machine) -> None:
    outputs = [
        host.run("setup", "--refresh", "--json").stdout,
        host.run("setup", "--refresh").stdout,
        host.run("status", "--json").stdout,
        host.run("setup", "--undo-rename", "--json").stdout,
        host.run("setup", "--refresh", "--rename").stdout,
    ]
    assert keys(host, "claude-desktop") == [SERVER]
    assert all(SECRET not in out for out in outputs)
