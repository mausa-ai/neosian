"""Installed Muse commands, in the environment Muse actually gives them."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope
from neosian._foundation.record.cli import run as record
from neosian._foundation.server.app import build_app
from tests.unit.record.payloads import prompt, session_start, stop, tool


def _install(root: Path, extra: list[str], env: dict[str, str]) -> str:
    base = root / "config" / "muse"
    base.mkdir(parents=True, exist_ok=True)
    child_env = {
        **os.environ,
        **env,
        "XDG_CONFIG_HOME": str(base.parent),
        "HOME": str(root),
    }
    child_env.pop("NEOSIAN_POSTGRES_DSN", None)
    done = subprocess.run(
        [
            str(Path(sys.executable).with_name("neosian")),
            "setup",
            "--client",
            "muse-code",
            "--write",
            "--json",
            *extra,
        ],
        env=child_env,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return str(json.loads(done.stdout)["clients"][0]["hooks"]["command"])


async def test_installed_shell_line_records_two_projects_and_startup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nh"
    command = _install(tmp_path, [], {"NEOSIAN_HOME": str(root)})
    # HOME and PATH survive Muse; NEOSIAN_HOME and other arbitrary variables do not.
    env = {
        name: os.environ[name]
        for name in ("PATH", "USER", "LOGNAME")
        if name in os.environ
    }
    env["HOME"] = str(tmp_path)
    contexts: list[str] = []
    for project in ("a", "b"):
        directory = tmp_path / project
        (directory / "src").mkdir(parents=True)
        for payload in (
            prompt(f"hello {project}", session=project),
            tool(session=project),
            stop("done", session=project),
        ):
            cwd = (
                directory / "src"
                if payload["hook_event_name"] == "PostToolUse"
                else directory
            )
            done = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=cwd,
                env=env,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 0 and done.stdout == "", done.stderr
        done = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=directory,
            env=env,
            input=json.dumps(session_start(session="fresh")),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0 and f"hello {project}" in done.stdout
        assert "muse-code:" in done.stdout
        contexts.append(done.stdout)
    assert "hello b" not in contexts[0] and "hello a" not in contexts[1]
    store = FileStore(root)
    for project in ("a", "b"):
        turns = await store.read_turns(project)
        assert len(turns) == 1 and turns[0].actor == f"muse-code:{project}"
        assert [m.role.value for m in turns[0].messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert await store.read(
            str(project_scope(tmp_path / project)), f"sessions/{project}"
        )
    assert not list(root.glob("*/proj%3Asrc"))


async def test_managed_record_reaches_authenticated_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shlex

    from neosian._foundation.server import remote as remote_module

    store = FileStore(tmp_path / "store")
    app = await build_app(store, token="test-token")
    connect = remote_module.RemoteStore.connect

    async def through_app(
        url: str, *, token: str, **_: Any
    ) -> remote_module.RemoteStore:
        return await connect(url, token=token, transport=httpx.ASGITransport(app=app))

    monkeypatch.setattr(remote_module.RemoteStore, "connect", through_app)
    env = {"NEOSIAN_CLIENT_TOKEN": "test-token", "NEOSIAN_HOME": str(tmp_path / "nh")}
    command = _install(tmp_path, ["--url", "http://state"], env)
    (tmp_path / "a").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    argv = shlex.split(command)[3:]
    for payload in (
        prompt("managed", session="managed"),
        tool(session="managed"),
        stop("done", session="managed"),
    ):
        out = io.StringIO()
        assert (
            await record(
                argv,
                {"NEOSIAN_CLIENT_TOKEN": "test-token"},
                stdin=io.StringIO(json.dumps(payload)),
                out=out,
                err=io.StringIO(),
            )
            == 0
        )
    turns = await store.read_turns("managed")
    assert len(turns) == 1 and turns[0].actor == "client:default/muse-code:managed"
    assert await store.read(str(project_scope(tmp_path / "a")), "sessions/managed")


async def test_muse_startup_is_utf8_bounded_and_json_envelope_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from neosian._foundation.record import cli

    async def context(*_args: Any, **_kwargs: Any) -> str:
        return "🙂" * 9000

    monkeypatch.setattr(cli, "render_session_start", context)
    argv = ["--root", str(tmp_path), "--scope", "user:me", "--agent", "muse-code"]
    text = json.dumps(session_start(session="fresh"))
    for mode in ([], ["--json"]):
        out = io.StringIO()
        assert (
            await record(
                [*argv, *mode], {}, stdin=io.StringIO(text), out=out, err=io.StringIO()
            )
            == 0
        )
        result = out.getvalue()
        if mode:
            assert json.loads(result)["context"] == "🙂" * 9000
        else:
            assert len(result.encode()) < 16384 and "�" not in result
