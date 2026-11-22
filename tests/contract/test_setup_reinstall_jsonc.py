"""OpenCode's commented `.jsonc` and going back that fails (DESIGN §40.53;
review of 0ded315, finding 1, and the confirming pass on 61aaff6, N1 to N5):
a `.jsonc` is read as OpenCode reads it, comments and trailing commas
allowed, so the plan, the search and the two-server checks know which keys
stand in it; comments never count; our entry there moves in place, every
comment kept; a file that still cannot be read gets one line; a going-back
that fails says the client is still on `neosian`."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.contract.refresh_support import FORMER, SERVER, Machine, machine
from tests.contract.support import Binary
from tests.contract.test_setup_reinstall import keys, released, rename_member

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


# Finding 1: OpenCode's commented `.jsonc`, read as OpenCode reads it.


def test_a_commented_jsonc_beside_json_moves_and_goes_back_comments_kept(
    host: Machine,
) -> None:
    path = jsonc(host)
    before = path.read_text()
    result = host.run("setup", "--refresh", "--json")
    assert result.returncode == 0, result.stdout
    member = rename_member(json.loads(result.stdout), "opencode")
    assert member["state"] == "moved" and member["stops"] == [], member
    assert keys(host, "opencode") == [SERVER]
    assert path.read_text() == before.replace(f'"{FORMER}"', f'"{SERVER}"')
    back = host.run("setup", "--undo-rename", "opencode")
    assert back.returncode == 0, back.stdout
    assert path.read_text() == before and keys(host, "opencode") == [FORMER]


def test_the_two_server_checks_count_a_commented_jsonc(host: Machine) -> None:
    host.refresh()
    assert keys(host, "opencode") == [SERVER]
    path = jsonc(host)
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert row["two_servers"][0]["old"] == f"{path} /mcp/{FORMER}"
    assert "on neosian and neosian-memory at once" in host.run("status").stdout
    assert host.run("setup", "--check").returncode == 1


def test_only_a_commented_jsonc_moves_in_place(host: Machine) -> None:
    (host.home("opencode") / "opencode.json").unlink()
    path = jsonc(host)
    before = path.read_text()
    assert "OpenCode moved to neosian." in host.run("setup", "--refresh").stdout
    assert path.read_text() == before.replace(f'"{FORMER}"', f'"{SERVER}"')
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert row["two_servers"] == []


def test_a_hand_rename_in_the_jsonc_is_ours_and_the_next_run_moves_the_rest(
    host: Machine,
) -> None:
    path = jsonc(host)
    path.write_text(path.read_text().replace(f'"{FORMER}"', f'"{SERVER}"'))
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert row["two_servers"][0]["new"] == f"{path} /mcp/{SERVER}"
    assert host.run("setup", "--check").returncode == 1
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "moved", member
    assert keys(host, "opencode") == [SERVER]
    status = json.loads(host.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert row["two_servers"] == [] and row["name"] == SERVER


# Confirming pass (N1 to N5): a commented `.jsonc` is read as OpenCode reads
# it, and our entry in it moves in place, every comment byte kept.


def commented(path: Path, servers: dict[str, Any], extra: str = "") -> str:
    """A `.jsonc` with a line comment, a block comment and a trailing comma."""
    body = ",\n    ".join(f'"{k}": {json.dumps(v)}' for k, v in servers.items())
    text = (
        '{\n  // my servers, see neosian-memory notes\n  /* kept */ "mcp": {\n    '
        + body
        + ",\n  },\n"
        + extra
        + "}\n"
    )
    path.write_text(text)
    return text


def ours_entry(host: Machine) -> dict[str, Any]:
    return {"type": "local", "command": [str(host.binary.executable), "mcp"]}


def two_free(host: Machine, project: Path | None = None) -> None:
    run = host if project is None else dataclasses.replace(host, project=project)
    status = json.loads(run.run("status", "--json").stdout)
    [row] = [c for c in status["clients"] if c["client"] == "opencode"]
    assert row["two_servers"] == [], row["two_servers"]
    check = json.loads(run.run("setup", "--check", "--json").stdout)
    assert not [i for i in check["issues"] if "two of neosian's servers" in i]


def test_our_entry_in_a_commented_jsonc_moves_in_place(host: Machine) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    text = commented(path, {FORMER: ours_entry(host)})
    report = host.refresh()
    assert rename_member(report, "opencode")["state"] == "moved"
    assert path.read_text() == text.replace(f'"{FORMER}"', f'"{SERVER}"')
    assert keys(host, "opencode") == [SERVER]
    two_free(host)


def test_comments_around_and_inside_our_entry_are_kept_byte_for_byte(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    command = json.dumps(str(host.binary.executable))
    text = (
        '{\n  /* "neosian-memory": {"command": ["x"]} */\n  "mcp": {\n'
        f'    "{FORMER}" /* ours */ : {{ // é\n'
        f'      "type": "local", "command": [{command}, "mcp",], // "a, }}"\n'
        "    },\n    // last\n  },\n}\n"
    )
    path.write_text(text)
    assert rename_member(host.refresh(), "opencode")["state"] == "moved"
    assert path.read_text() == text.replace(f'"{FORMER}" /*', f'"{SERVER}" /*')
    back = host.run("setup", "--undo-rename", "opencode")
    assert back.returncode == 0, back.stdout
    assert path.read_text() == text


def test_a_project_jsonc_moves_from_the_project_and_home_stays_true(
    binary: Binary,
) -> None:
    project = binary.directory / "work"
    project.mkdir()
    host = dataclasses.replace(machine(binary), project=project)
    host.setup()
    released(host)
    path = project / "opencode.jsonc"
    text = commented(path, {FORMER: ours_entry(host)})
    two_free(host)
    host.refresh()
    assert path.read_text() == text.replace(f'"{FORMER}"', f'"{SERVER}"')
    two_free(host)
    home = dataclasses.replace(host, project=binary.directory)
    home.refresh()
    two_free(home)
    assert path.read_text() == text.replace(f'"{FORMER}"', f'"{SERVER}"')


def test_a_comment_that_only_mentions_the_old_name_does_not_block(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    text = commented(path, {"other": {"type": "local", "command": ["/opt/other"]}})
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "moved" and member["stops"] == [], member
    assert path.read_text() == text


def test_another_server_named_neosian_in_a_commented_jsonc_is_a_collision(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    commented(path, {SERVER: {"type": "remote", "url": "https://other.example/mcp"}})
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "left"
    assert [(s["kind"], Path(s["path"]).name) for s in member["stops"]] == [
        ("taken", "opencode.jsonc")
    ]


def test_neosian_as_a_string_value_in_a_commented_jsonc_is_nothing(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    commented(
        path,
        {"other": {"type": "local", "command": ["neosian"]}},
        '  "model": "neosian",\n',
    )
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "moved" and member["stops"] == [], member


def test_a_file_that_still_cannot_be_read_gets_one_line(host: Machine) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    path.write_text('{\n  "mcp": {"neosian-memory": {"type": "local", \n')
    text = host.run("setup", "--refresh").stdout
    lines = [ln for ln in text.splitlines() if str(path) in ln]
    assert len(lines) == 1, lines
    assert "cannot be read, and its text contains neosian-memory" in lines[0]
    assert keys(host, "opencode") == [FORMER]


@pytest.mark.parametrize("case", ["refusing-cli", "read-only"])
def test_going_back_that_fails_while_writing_says_the_client_is_still_on_neosian(
    host: Machine, case: str
) -> None:
    host.refresh()
    if case == "refusing-cli":
        label, client = "Claude Code", "claude-code"
        result = host.run("setup", "--undo-rename", client, CLAUDE_FAIL="mcp add-json")
    else:
        label, client = "Cursor", "cursor"
        cursor = host.root / ".cursor/mcp.json"
        cursor.chmod(0o444)
        try:
            result = host.run("setup", "--undo-rename", client)
        finally:
            cursor.chmod(0o644)
    assert result.returncode == 1, result.stdout
    assert f"{label} could not go back (" in result.stdout
    assert "it is still on neosian." in result.stdout
    assert "back on neosian-memory" not in result.stdout
    assert keys(host, client) == [SERVER]


# Closing rulings on abd0ae6 (findings 1 to 3).

FOREIGN = {"type": "remote", "url": "https://theirs.example/mcp"}


def bare_cr(path: Path, key: str, entry: dict[str, Any]) -> bytes:
    """A line comment ended by a bare CR, as OpenCode's parser ends it."""
    data = f'{{\n  "a": 1, // c\r  "mcp": {{"{key}": {json.dumps(entry)}}}\n}}\n'
    path.write_bytes(data.encode())
    return data.encode()


def test_a_bare_cr_ends_a_comment_so_another_servers_neosian_is_a_collision(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    data = bare_cr(path, SERVER, FOREIGN)
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "left"
    assert [(s["kind"], Path(s["path"]).name) for s in member["stops"]] == [
        ("taken", "opencode.jsonc")
    ]
    assert keys(host, "opencode") == [FORMER] and path.read_bytes() == data


def test_a_bare_cr_ends_a_comment_so_our_old_key_is_seen_and_moved(
    host: Machine,
) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    data = bare_cr(path, FORMER, ours_entry(host))
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "moved", member
    assert keys(host, "opencode") == [SERVER]
    assert path.read_bytes() == data.replace(
        f'"{FORMER}"'.encode(), f'"{SERVER}"'.encode()
    )


@pytest.mark.parametrize(
    "shape,after",
    [
        (
            '{{\n  // mine\n  "mcp": {{"neosian": {e},\n    /* again */ '
            '"neosian-memory": {e},\n  }}\n}}\n',
            '{{\n  // mine\n  "mcp": {{"neosian": {e}\n    /* again */,\n  }}\n}}\n',
        ),
        (
            '{{\n  "mcp": {{\n    "neosian-memory": {e}, // the old one\n'
            '    "neosian": {e}\n  }}\n}}\n',
            '{{\n  "mcp": {{\n     // the old one\n    "neosian": {e}\n  }}\n}}\n',
        ),
    ],
    ids=["comment-before-a-last-old-member", "comment-after-a-first-old-member"],
)
def test_removing_the_old_key_beside_ours_keeps_every_comment(
    host: Machine, shape: str, after: str
) -> None:
    (host.home("opencode") / "opencode.json").unlink()
    path = host.home("opencode") / "opencode.jsonc"
    entry = json.dumps(ours_entry(host))
    path.write_text(shape.format(e=entry))
    member = rename_member(host.refresh(), "opencode")
    assert member["state"] == "moved" and member["stops"] == [], member
    assert path.read_text() == after.format(e=entry)
    two_free(host)


def test_a_removal_that_cannot_be_made_says_why_and_advises_no_failing_command(
    host: Machine,
) -> None:
    (host.home("opencode") / "opencode.json").unlink()
    path = host.home("opencode") / "opencode.jsonc"
    entry = json.dumps(ours_entry(host))
    before = (
        f'{{\n  // mine\n  "mcp": {{\n    "{FORMER}": {entry},\n'
        f'    "{FORMER}": {entry},\n    "{SERVER}": {entry},\n  }},\n}}\n'
    )
    path.write_text(before)
    refresh = host.run("setup", "--refresh")
    assert refresh.returncode == 1, refresh.stdout
    assert "a key given twice" in refresh.stdout
    texts = {
        "refresh": refresh.stdout,
        "status": host.run("status").stdout,
        "check": host.run("setup", "--check").stdout,
        "setup": host.run("setup", "--client", "opencode", "--write").stdout,
    }
    for name, text in texts.items():
        assert "--refresh --rename" not in text, (name, text)
    for name in ("refresh", "status", "check"):
        assert "delete neosian-memory there by hand" in texts[name], name
    assert path.read_text() == before


def test_a_mention_in_a_commented_jsonc_reports_its_real_line(host: Machine) -> None:
    path = host.home("opencode") / "opencode.jsonc"
    path.write_text(
        '{\n  // mine\n  "agent": {\n    "x": {"prompt": "use neosian-memory"}\n  }\n}\n'
    )
    result = host.run("setup", "--refresh", "--json")
    member = rename_member(json.loads(result.stdout), "opencode")
    assert member["state"] == "left"
    assert [(Path(m["path"]).name, m["line"]) for m in member["mentions"]] == [
        ("opencode.jsonc", 4)
    ]
    assert f"{path}:4 names neosian" in host.run("setup", "--refresh").stdout
