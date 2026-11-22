"""The reinstall after its independent review (DESIGN §40.53; review of
0ded315, findings 2 and 3): a file that cannot be written leaves the client
with exit 0; going back and the output say only what is true. Finding 1 and
the confirming pass (OpenCode's commented `.jsonc`) are in
test_setup_reinstall_jsonc.py."""

from __future__ import annotations

import dataclasses
import json
import os
import time
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


def test_a_repeat_setup_leaves_every_receipt_as_it_was(binary: Binary) -> None:
    """The receipt keeps its bytes when the same entry is written again, so
    a repeat setup changes no file (test_setup.py's repeat contract held
    only within one clock second)."""
    host = machine(binary)
    host.setup()
    receipts = sorted(host.root.rglob("neosian-registrations.json"))
    assert receipts
    before = [path.read_bytes() for path in receipts]
    time.sleep(1.1)
    host.setup()
    assert [path.read_bytes() for path in receipts] == before
