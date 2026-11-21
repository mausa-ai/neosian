"""The reinstall after its independent review (DESIGN §40.53; review of
0ded315, findings 1 to 3): a file the client reads and neosian cannot read
or edit stops the move both ways and counts in the two-server checks; a
file that cannot be written leaves the client with exit 0; going back and
the output say only what is true."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.contract.refresh_support import (
    FORMER,
    SERVER,
    Machine,
    claude_json,
    claude_project,
    machine,
)
from tests.contract.support import Binary
from tests.contract.test_setup_reinstall import (
    CLIENTS,
    desktop,
    edit,
    keys,
    released,
    rename_member,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")


@pytest.fixture
def host(binary: Binary) -> Machine:
    machine_ = machine(binary)
    machine_.setup()
    released(machine_)
    return machine_


def jsonc(host: Machine) -> Path:
    path = host.home("opencode") / "opencode.jsonc"
    entry = json.dumps(
        {"type": "local", "command": [str(host.binary.executable), "mcp"]}
    )
    path.write_text(
        '{\n  // my servers\n  "mcp": {"' + FORMER + '": ' + entry + "}\n}\n"
    )
    return path


# Finding 1: OpenCode's commented `.jsonc`.


def test_a_commented_jsonc_beside_json_stops_opencode_both_ways(host: Machine) -> None:
    path = jsonc(host)
    before = path.read_text()
    for args in (
        ("setup", "--refresh", "--json"),
        ("setup", "--refresh", "--rename", "--json"),
    ):
        result = host.run(*args)
        assert result.returncode == 0, result.stdout
        member = rename_member(json.loads(result.stdout), "opencode")
        assert member["state"] == "left", member
        assert [(s["kind"], Path(s["path"]).name) for s in member["stops"]] == [
            ("unedited", "opencode.jsonc")
        ]
    assert keys(host, "opencode") == [FORMER] and path.read_text() == before
    text = host.run("setup", "--refresh", "--rename").stdout
    assert f"{path} names neosian's server and neosian cannot read or edit it" in text
    assert (
        "rename the key there to neosian, then run neosian setup --refresh --rename"
        in text
    )


def test_the_two_server_checks_count_a_commented_jsonc(host: Machine) -> None:
    host.refresh()
    assert keys(host, "opencode") == [SERVER]
    jsonc(host)
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert (
        row["two_servers"] and "neosian cannot read it" in row["two_servers"][0]["old"]
    )
    assert "on neosian and neosian-memory at once" in host.run("status").stdout
    assert host.run("setup", "--check").returncode == 1


def test_only_a_commented_jsonc_says_opencode_stays_and_why(host: Machine) -> None:
    (host.home("opencode") / "opencode.json").unlink()
    path = jsonc(host)
    text = host.run("setup", "--refresh").stdout
    assert "OpenCode stays on neosian-memory, which keeps working." in text
    assert f"{path} names neosian's server" in text


# Finding 2: a file that cannot be written leaves the client, exit 0.


def readonly_cursor(host: Machine) -> Path:
    path = host.root / ".cursor/mcp.json"
    path.chmod(0o444)
    return path


def duplicate_desktop(host: Machine) -> Path:
    path = desktop(host)
    entry = json.dumps({"command": str(host.binary.executable), "args": ["mcp"]})
    path.write_text(
        '{"mcpServers": {"x": 1}, "mcpServers": {"' + FORMER + '": ' + entry + "}}"
    )
    return path


@pytest.mark.parametrize(
    ("client", "spoil", "kind"),
    [
        ("cursor", readonly_cursor, "unwritable"),
        ("claude-desktop", duplicate_desktop, "not_editable"),
    ],
    ids=["read-only", "duplicate-key"],
)
def test_a_file_that_cannot_be_written_leaves_the_client_with_exit_0(
    host: Machine, client: str, spoil: Any, kind: str
) -> None:
    path = spoil(host)
    before = path.read_bytes()
    try:
        report = host.refresh()
    finally:
        path.chmod(0o644)
    member = rename_member(report, client)
    assert member["state"] == "left", member
    assert [(s["kind"], s["at"]) for s in member["stops"]] == [("unwritable", kind)]
    assert path.read_bytes() == before
    assert all(
        rename_member(report, c)["state"] == "moved" for c in CLIENTS if c != client
    )


# Finding 3: true and single lines.


def test_going_back_after_a_collision_lists_it_and_exits_0(host: Machine) -> None:
    host.refresh()
    edit(
        host.root / ".cursor/mcp.json",
        lambda d: d["mcpServers"].update({SERVER: {"command": "theirs"}}),
    )
    result = host.run("setup", "--undo-rename")
    assert result.returncode == 0, result.stdout
    assert "Cursor is back on neosian-memory." not in result.stdout
    assert "Codex is back on neosian-memory." in result.stdout
    assert result.stdout.count("neosian there is not the entry neosian wrote") == 1


def test_from_the_home_folder_cursors_one_file_is_reported_once(binary: Binary) -> None:
    host = dataclasses.replace(machine(binary), project=binary.directory)
    host.setup()
    released(host)
    edit(
        host.root / ".cursor/mcp.json",
        lambda d: d["mcpServers"].update({SERVER: {"command": "theirs"}}),
    )
    member = rename_member(host.refresh(), "cursor")
    assert [s["kind"] for s in member["stops"]] == ["taken"], member["stops"]


def test_a_rolled_back_failure_says_it_could_not_move(host: Machine) -> None:
    project = claude_project(host, "work", {})
    entry = json.loads(claude_json(host).read_text())["mcpServers"][FORMER]
    edit(
        claude_json(host),
        lambda d: d["projects"][str(project.resolve())].update(
            mcpServers={FORMER: entry}
        ),
    )
    text = host.run(
        "setup", "--refresh", CLAUDE_FAIL="mcp add-json --scope local"
    ).stdout
    assert (
        "Claude Code could not move (" in text
        and "it is back on neosian-memory." in text
    )


def test_cursor_and_desktop_moves_ask_to_check_their_own_switch(host: Machine) -> None:
    text = host.run("setup", "--refresh").stdout
    for label in ("Cursor", "Claude Desktop"):
        assert (
            f"If neosian was switched off in {label}'s own settings, check that it still is."
            in text
        )


# Pi's project override and each client-written disable.


def test_pis_project_override_moves_with_it(host: Machine) -> None:
    project = host.root / "pi-project"
    (project / ".pi").mkdir(parents=True)
    (project / ".pi/mcp.json").write_text(
        json.dumps({"mcpServers": {FORMER: {"enabled": False}}})
    )
    (host.home("pi") / "trust.json").write_text(
        json.dumps({str(project.resolve()): True})
    )
    host.refresh()
    assert json.loads((project / ".pi/mcp.json").read_text()) == {
        "mcpServers": {SERVER: {"enabled": False}}
    }


def claude_local_disable(host: Machine) -> None:
    project = claude_project(host, "work", {})
    edit(
        claude_json(host),
        lambda d: d["projects"][str(project.resolve())].update(
            disabledMcpServers=[FORMER]
        ),
    )


def claude_mcpjson_rejected(host: Machine) -> None:
    project = claude_project(host, "work", {})
    edit(
        claude_json(host),
        lambda d: d["projects"][str(project.resolve())].update(
            disabledMcpjsonServers=[FORMER]
        ),
    )


def opencode_tool_off(host: Machine) -> None:
    edit(
        host.home("opencode") / "opencode.json",
        lambda d: d.update(tools={"neosian-memory_harness": False}),
    )


@pytest.mark.parametrize(
    ("client", "disable"),
    [
        ("claude-code", claude_local_disable),
        ("claude-code", claude_mcpjson_rejected),
        ("opencode", opencode_tool_off),
    ],
    ids=["claude-mcp-disable", "claude-mcpjson-rejected", "opencode-tool-off"],
)
def test_each_client_written_disable_blocks(
    host: Machine, client: str, disable: Any
) -> None:
    disable(host)
    member = rename_member(host.refresh(), client)
    assert member["state"] == "left" and member["mentions"], member
    assert keys(host, client) == [FORMER]
