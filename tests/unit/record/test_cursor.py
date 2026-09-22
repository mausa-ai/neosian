"""Cursor's native record, and coexistence with imported Claude hooks."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from neosian._cli.setup import run_setup as setup
from neosian._cli.status import client_status
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope, user_scope
from neosian._foundation.record.cli import run
from neosian._foundation.record.cursor_install import EVENTS
from neosian._foundation.record.span import parse_payload, reduce_payload
from neosian._foundation.record.targets import installed_argv, resolve_target
from tests.unit.record.test_install import _context, _run


def payload(event: str, project: Path, **fields: Any) -> dict[str, Any]:
    return {
        "conversation_id": "cursor-session",
        "generation_id": "generation-1",
        "cursor_version": "2026.09.10-fd3934a",
        "hook_event_name": event,
        "workspace_roots": [str(project)],
        **fields,
    }


async def record(
    root: Path,
    data: dict[str, Any],
    *args: str,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        ["--root", str(root / "store"), "--spool", str(root / "spool"), *args],
        {},
        stdin=io.StringIO(json.dumps(data)),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


async def test_native_turn_and_imported_hooks_record_only_once(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    events = (
        payload("beforeSubmitPrompt", project, prompt="hello"),
        payload(
            "postToolUse",
            project,
            tool_name="Shell",
            tool_use_id="call-1",
            tool_input={"command": "pwd"},
            tool_output='{"stdout":"/project"}',
        ),
        payload("afterAgentResponse", project, text="finished"),
    )
    for data in events:
        # The same native payload also reaches Claude's imported command.
        code, out, _ = await record(tmp_path, data, "--json")
        assert code == 0 and json.loads(out)["disposition"] == "ignored"
        assert (await record(tmp_path, data, "--agent", "cursor"))[:2] == (0, "{}\n")
    store = FileStore(tmp_path / "store")
    assert not await store.read_turns("cursor-session")
    stop = payload("stop", project, status="completed")
    assert (await record(tmp_path, stop, "--agent", "cursor"))[0] == 0
    assert (await record(tmp_path, stop))[0] == 0
    # Repeated stops carry no text and cannot create another turn.
    assert (await record(tmp_path, stop, "--agent", "cursor"))[0] == 0
    turns = await store.read_turns("cursor-session")
    assert len(turns) == 1 and turns[0].actor == "cursor:cursor-session"
    assert [m.role.value for m in turns[0].messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert turns[0].messages[2].content == '{"stdout":"/project"}'
    assert turns[0].messages[-1].content == "finished"
    assert await store.read(str(project_scope(project)), "sessions/cursor-session")
    code, out, _ = await record(
        tmp_path, payload("sessionStart", project), "--agent", "cursor"
    )
    assert code == 0 and "hello" in json.loads(out)["additional_context"]
    code, out, _ = await record(
        tmp_path, payload("sessionStart", project), "--agent", "cursor", "--json"
    )
    assert code == 0 and "hello" in json.loads(out)["context"]


@pytest.mark.parametrize("roots", [[], ["/a", "/b"], ["relative"], None])
async def test_ambiguous_project_uses_only_user_mount(
    tmp_path: Path, roots: Any
) -> None:
    for event, fields in (
        ("beforeSubmitPrompt", {"prompt": "ambiguous"}),
        ("stop", {}),
        ("afterAgentResponse", {"text": "answer"}),
    ):
        code, _, err = await record(
            tmp_path,
            payload(event, tmp_path, workspace_roots=roots, **fields),
            "--agent",
            "cursor",
        )
        assert code == 0 and "using /user" in err
    store = FileStore(tmp_path / "store")
    assert await store.read(str(user_scope()), "sessions/cursor-session")


@pytest.mark.parametrize("override", ["project", "scope", "mount"])
async def test_explicit_selection_wins(tmp_path: Path, override: str) -> None:
    directory = tmp_path / "chosen"
    args = {
        "project": ["--project", str(directory)],
        "scope": ["--scope", "user:chosen"],
        "mount": ["--mount", "scope=user:chosen,path=project"],
    }[override]
    for event in ("beforeSubmitPrompt", "stop", "afterAgentResponse"):
        code, _, err = await record(
            tmp_path,
            payload(event, tmp_path, workspace_roots=[], prompt="chosen"),
            "--agent",
            "cursor",
            *args,
        )
        assert code == 0 and not err
    scope = str(project_scope(directory)) if override == "project" else "user:chosen"
    assert await FileStore(tmp_path / "store").read(scope, "sessions/cursor-session")


def test_cursor_output_remains_bounded_and_subagents_are_skipped(
    tmp_path: Path,
) -> None:
    data = payload("postToolUse", tmp_path, tool_output="x" * 5000)
    normalized = parse_payload(json.dumps(data), agent="cursor")
    reduced = reduce_payload(normalized)[1]
    assert reduced and reduced["response"].endswith("[truncated 904 chars]")
    assert reduce_payload({**normalized, "agent_id": "child"}) == ("skipped", None)


async def test_bad_cursor_payload_is_runtime_error(tmp_path: Path) -> None:
    code, out, _ = await record(
        tmp_path, {"hook_event_name": "stop"}, "--agent", "cursor"
    )
    assert code == 1 and out == ""
    assert not (tmp_path / "spool").exists()


def test_install_preserves_reinstalls_and_status_reads_flat_hooks(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    (context.home / ".cursor").mkdir()
    target = resolve_target("cursor", context)
    target.config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "secret": "not-in-preview",
                "hooks": {"stop": [{"command": "other"}]},
            }
        )
    )
    argv = ["--client", "cursor", "--root", str(tmp_path / "store")]
    for _ in range(2):
        code, out, _ = _run([*argv, "--write", "--json"], context)
        assert code == 0 and "not-in-preview" not in out
    written = json.loads(target.config_path.read_text())
    assert written["secret"] == "not-in-preview"
    assert set(written["hooks"]) == set(EVENTS)
    assert len(written["hooks"]["stop"]) == 2
    installed = installed_argv(target)
    assert installed and "cursor" in installed and "--mount" not in installed
    status = client_status("cursor", context)
    assert status.hooks_present and status.level == "user"
    code, _, err = _run([*argv, "--level", "project", "--write"], context)
    assert code == 1 and "both would fire" in err


def test_preflight_prevents_displacement_and_setup_half_write(tmp_path: Path) -> None:
    context = _context(tmp_path)
    (context.home / ".cursor").mkdir()
    argv = ["--client", "cursor", "--root", str(tmp_path / "store")]
    assert _run([*argv, "--level", "project", "--write"], context)[0] == 0
    project = resolve_target("cursor", context, "project").config_path
    before = project.read_bytes()
    user = resolve_target("cursor", context).config_path
    user.write_text('{"version":2,"hooks":{}}')
    assert _run([*argv, "--write"], context)[0] == 1
    assert project.read_bytes() == before
    out = io.StringIO()
    assert (
        setup(
            [*argv, "--write", "--json"],
            {},
            context=context,
            out=out,
            err=io.StringIO(),
        )
        == 1
    )
    assert not (context.home / ".cursor/mcp.json").exists()
    user.unlink()
    assert _run([*argv, "--write"], context)[0] == 0
    assert not json.loads(project.read_text())["hooks"]


def test_bad_mcp_configuration_prevents_setup_hook_write(tmp_path: Path) -> None:
    context = _context(tmp_path)
    base = context.home / ".cursor"
    base.mkdir()
    (base / "mcp.json").write_text('{"mcpServers":[]}')
    out = io.StringIO()
    code = setup(
        ["--client", "cursor", "--write", "--json"],
        {},
        context=context,
        out=out,
        err=io.StringIO(),
    )
    assert code == 1 and not (base / "hooks.json").exists()
    row = json.loads(out.getvalue())["clients"][0]
    assert not row["mcp"]["success"] and not row["hooks"]["applied"]
    assert (base / "mcp.json").read_text() == '{"mcpServers":[]}'
