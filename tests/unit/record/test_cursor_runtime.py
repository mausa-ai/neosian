"""Installed Cursor commands, real event order and failed-land recovery."""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope
from neosian._foundation.record.cli import run
from neosian._foundation.server.app import build_app
from tests.unit.record.test_cursor import payload, record


def install(root: Path, *extra: str) -> tuple[str, dict[str, str]]:
    (root / ".cursor").mkdir(exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("API_KEY")
        and k not in ("NEOSIAN_POSTGRES_DSN", "NEOSIAN_SCOPE")
    }
    env.update(
        HOME=str(root), NEOSIAN_HOME=str(root / "nh"), NEOSIAN_CLIENT_TOKEN="test-token"
    )
    done = subprocess.run(
        [
            str(Path(sys.executable).with_name("neosian")),
            "setup",
            "--client",
            "cursor",
            "--write",
            "--json",
            *extra,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    hooks_text = (root / ".cursor/hooks.json").read_text()
    # Model Cursor's broken comment stripper: a literal URL would truncate JSON.
    import re

    hooks = json.loads(re.sub(r"//.*$", "", hooks_text, flags=re.MULTILINE))
    command: str = hooks["hooks"]["stop"][0]["command"]
    return command, env


async def test_installed_command_uses_payload_projects_from_cursor_home(
    tmp_path: Path,
) -> None:
    command, env = install(tmp_path)
    contexts = []
    for project in ("alpha", "beta"):
        directory = tmp_path / project
        directory.mkdir()
        events = (
            payload("beforeSubmitPrompt", directory, prompt=f"hello {project}"),
            payload(
                "postToolUse",
                directory,
                tool_name="Shell",
                tool_input={"command": "pwd"},
                tool_output='{"output":"ok"}',
                cwd="/elsewhere",
            ),
            payload("stop", directory, status="completed"),
            payload("afterAgentResponse", directory, text=f"answer {project}"),
            payload("sessionStart", directory),
        )
        for event in events:
            event["conversation_id"] = project
            done = subprocess.run(
                ["/bin/sh", "-c", command],
                input=json.dumps(event),
                cwd=tmp_path / ".cursor",
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 0, done.stderr
        contexts.append(json.loads(done.stdout)["additional_context"])
    assert "hello alpha" in contexts[0] and "hello beta" not in contexts[0]
    assert "hello beta" in contexts[1] and "hello alpha" not in contexts[1]
    store = FileStore(tmp_path / "nh")
    for project in ("alpha", "beta"):
        turns = await store.read_turns(project)
        assert len(turns) == 1 and turns[0].messages[-1].content == f"answer {project}"
        assert await store.read(
            str(project_scope(tmp_path / project)), f"sessions/{project}"
        )
        assert not (tmp_path / project / ".cursor").exists()


async def test_installed_remote_command_and_failed_stop_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from neosian._foundation.server import remote as module

    store = FileStore(tmp_path / "store")
    app = await build_app(store, token="test-token")
    connect = module.RemoteStore.connect

    async def through_app(url: str, *, token: str, **_: Any) -> module.RemoteStore:
        return await connect(url, token=token, transport=httpx.ASGITransport(app=app))

    monkeypatch.setattr(module.RemoteStore, "connect", through_app)
    command, _ = install(tmp_path, "--url", "http://state")
    argv = shlex.split(command)[3:]
    events = [
        payload("beforeSubmitPrompt", tmp_path, prompt="retain me"),
        payload("stop", tmp_path),
        payload("afterAgentResponse", tmp_path, text="answer"),
    ]
    for data in events:
        out = io.StringIO()
        code = await run(
            argv,
            {"NEOSIAN_CLIENT_TOKEN": "wrong"},
            stdin=io.StringIO(json.dumps(data)),
            out=out,
            err=io.StringIO(),
        )
    assert code == 1 and out.getvalue() == ""
    assert (tmp_path / "nh/spool/cursor-session.jsonl").exists()
    assert not await store.read_turns("cursor-session")
    # Retry the failed landing via Stop, retaining the answer already spooled.
    code = await run(
        argv,
        {"NEOSIAN_CLIENT_TOKEN": "test-token"},
        stdin=io.StringIO(json.dumps(payload("stop", tmp_path))),
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert code == 0
    turns = await store.read_turns("cursor-session")
    assert len(turns) == 1 and turns[0].actor == "client:default/cursor:cursor-session"
    assert [m.content for m in turns[0].messages] == ["retain me", "answer"]
    assert not (tmp_path / "nh/spool/cursor-session.jsonl").exists()


@pytest.mark.parametrize("status", ["aborted", "error"])
async def test_failed_turn_lands_without_response(tmp_path: Path, status: str) -> None:
    await record(
        tmp_path,
        payload("beforeSubmitPrompt", tmp_path, prompt="unfinished"),
        "--agent",
        "cursor",
    )
    await record(
        tmp_path, payload("stop", tmp_path, status=status), "--agent", "cursor"
    )
    turns = await FileStore(tmp_path / "store").read_turns("cursor-session")
    assert len(turns) == 1 and turns[0].messages[0].content == "unfinished"


def test_mcp_forwards_credential_names_without_values(tmp_path: Path) -> None:
    install(tmp_path, "--url", "http://state")
    entry = json.loads((tmp_path / ".cursor/mcp.json").read_text())["mcpServers"][
        "neosian-memory"
    ]
    assert entry["env"] == {"NEOSIAN_CLIENT_TOKEN": "${env:NEOSIAN_CLIENT_TOKEN}"}
    assert "test-token" not in json.dumps(entry)


async def test_concurrent_stop_and_response_land_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    original = FileStore.append_turn
    calls = 0

    async def delayed(self: FileStore, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)  # another hook arrives during the store write
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(FileStore, "append_turn", delayed)
    await record(
        tmp_path,
        payload("beforeSubmitPrompt", tmp_path, prompt="one"),
        "--agent",
        "cursor",
    )
    await record(tmp_path, payload("stop", tmp_path), "--agent", "cursor")
    results = await asyncio.gather(
        record(
            tmp_path,
            payload("afterAgentResponse", tmp_path, text="answer"),
            "--agent",
            "cursor",
        ),
        record(tmp_path, payload("stop", tmp_path), "--agent", "cursor"),
    )
    assert all(code == 0 for code, _, _ in results)
    assert calls == 1
    turns = await FileStore(tmp_path / "store").read_turns("cursor-session")
    assert len(turns) == 1 and turns[0].messages[-1].content == "answer"
