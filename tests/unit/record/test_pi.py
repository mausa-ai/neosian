"""Pi's installer and the recorder boundary, keylessly (NU3)."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from neosian._cli.setup import present_clients, run_setup
from neosian._cli.status import client_status
from neosian._foundation.mcp.install import run_install as mcp_install
from neosian._foundation.mcp.targets import session_start_door
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.sessions import parse_sessions_document
from neosian._foundation.record.cli import run as record
from neosian._foundation.record.install import run_install
from neosian._foundation.record.targets import installed_argv, resolve_target
from neosian._foundation.shared.client_config import Environment
from neosian._foundation.shared.prompt_assets import get_prompt
from tests.unit.record.payloads import prompt, session_start, stop, tool


@pytest.fixture
def context(tmp_path: Path) -> Environment:
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    (tmp_path / "project").mkdir()
    return Environment(tmp_path, tmp_path / "project", "darwin", {}, sys.executable)


def invoke(
    context: Environment, *args: str, kind: str = "setup"
) -> tuple[int, dict[str, Any]]:
    out, err = io.StringIO(), io.StringIO()
    run = {"setup": run_setup, "mcp": mcp_install, "hooks": run_install}[kind]
    code = run(
        ["--client", "pi", "--json", *args],
        {"NEOSIAN_HOME": str(context.home / "nh"), "NEOSIAN_CLIENT_TOKEN": "secret"},
        context=context,
        out=out,
        err=err,
    )
    return code, json.loads(out.getvalue())


def test_preview_apply_and_status(context: Environment) -> None:
    base = context.home / ".pi" / "agent"
    path = base / "mcp.json"
    original = {"mcpServers": {"other": {"command": "other"}}, "theme": "dark"}
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    assert present_clients(context) == ["pi"]
    code, preview = invoke(context)
    assert code == 0 and path.read_bytes() == before
    assert not (base / "extensions").exists()
    plugin = preview["clients"][0]["hooks"]["plugin"]
    assert "__NEOSIAN_RECORD_ARGV__" not in plugin
    assert 'pi.on("agent_settled"' in plugin
    for _ in range(2):
        assert invoke(context, "--write")[0] == 0
    doc = json.loads(path.read_text())
    assert doc["theme"] == "dark" and doc["mcpServers"]["other"] == {"command": "other"}
    entry = doc["mcpServers"]["neosian-memory"]
    assert entry["exposure"] == "direct" and "--mount" not in entry["args"]
    assert entry["args"][-2:] == ["--actor", "mcp:pi"]
    target = resolve_target("pi", context)
    assert target.config_path.read_text() == plugin
    argv = installed_argv(target)
    assert argv is not None and argv[0] == sys.executable
    assert argv[argv.index("--agent") + 1] == "pi"
    status = client_status("pi", context)
    assert status.installed and status.mcp_registered and status.hooks_present
    assert status.level == "user" and list(context.cwd.iterdir()) == []
    assert session_start_door("mcp:pi") == "hook"


def test_override_and_missing_evidence(context: Environment) -> None:
    base = context.home / "nonstandard"
    context = replace(context, env={"PI_CODING_AGENT_DIR": str(base)})
    assert present_clients(context) == []
    assert invoke(context, "--write")[0] == 1 and not base.exists()
    base.mkdir()
    assert invoke(context, "--write")[0] == 0
    assert (base / "mcp.json").is_file()
    assert (base / "extensions" / "neosian-record.ts").is_file()


def test_project_transition_and_duplicate_refusal(context: Environment) -> None:
    assert invoke(context, "--level", "project", "--write")[0] == 0
    project = resolve_target("pi", context, "project")
    argv = installed_argv(project)
    assert argv is not None and "--mount" in argv
    assert invoke(context, "--write")[0] == 0
    assert not project.config_path.exists()
    assert not (context.cwd / ".pi" / "mcp.json").read_text().count("neosian-memory")
    user = resolve_target("pi", context)
    before = user.config_path.read_bytes()
    assert invoke(context, "--level", "project", "--write")[0] == 1
    assert user.config_path.read_bytes() == before
    assert not project.config_path.exists()


@pytest.mark.parametrize("contents", ["not json", "[]", '{"mcpServers":[]}'])
def test_invalid_mcp_refused_before_either_half(
    context: Environment, contents: str
) -> None:
    assert invoke(context, "--level", "project", "--write")[0] == 0
    project = resolve_target("pi", context, "project")
    before = project.config_path.read_bytes()
    path = context.home / ".pi" / "agent" / "mcp.json"
    path.write_text(contents)
    assert invoke(context, "--write")[0] == 1
    assert path.read_text() == contents
    assert project.config_path.read_bytes() == before


def test_remote_credentials_never_written(context: Environment) -> None:
    code, result = invoke(context, "--url", "http://state", "--write")
    assert code == 0 and "secret" not in json.dumps(result)
    for half in ("mcp", "hooks"):
        path = Path(result["clients"][0][half]["config_path"])
        assert "http://state" in path.read_text() and "secret" not in path.read_text()


async def test_pi_rounds_lineage_and_start_context(tmp_path: Path) -> None:
    root = tmp_path / "store"
    argv = [
        "--agent",
        "pi",
        "--root",
        str(root),
        "--scope",
        "user:me",
        "--spool",
        str(tmp_path / "spool"),
    ]

    async def send(payload: dict[str, Any]) -> str:
        out, err = io.StringIO(), io.StringIO()
        code = await record(
            argv, {}, stdin=io.StringIO(json.dumps(payload)), out=out, err=err
        )
        assert code == 0, err.getvalue()
        return out.getvalue()

    await send(prompt("continue"))
    await send(
        tool(
            "mcp__neosian_memory__continue_session",
            tool_use_id="outer/1",
            tool_input={},
            response={
                "content": [
                    {"type": "text", "text": "[continuing conversation earlier]"}
                ],
                "isError": False,
            },
        )
    )
    result = "start" + "x" * 50_000 + "tail"
    await send(
        tool(
            "bash",
            tool_use_id="outer/2",
            response={
                "content": [{"type": "text", "text": result}],
                "isError": True,
            },
        )
    )
    await send(stop("recorded"))
    await send(prompt("second prompt"))
    await send(stop("second answer"))
    store = FileStore(root)
    session = str(prompt()["session_id"])
    turns = await store.read_turns(session)
    assert len(turns) == 2 and turns[0].actor == f"pi:{session}"
    assert result in str(turns[0].messages[4].content)
    assert turns[0].messages[4].tool_call_id == "outer/2"
    doc = await store.read("user:me", f"sessions/{session}")
    assert doc is not None
    parsed = parse_sessions_document(doc.content)
    assert parsed is not None and parsed.continues == ("earlier",)
    text = await send(session_start("resume", session="fresh"))
    assert len(text.rstrip()) <= 10_000 and "second prompt" in text
    assert get_prompt("context.start_instructions") in text
    compact = await send(session_start("compact"))
    assert "second prompt" in compact and len(compact.rstrip()) <= 10_000
    assert get_prompt("context.start_instructions") in compact
    assert len(await store.read_turns(session)) == 2
