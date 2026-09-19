"""NU2's done-when, in one keyless pin (DESIGN §22.6): one machine
registered once serves every project.

`neosian setup --write` runs once. After it no project carries a file, yet
a session in any directory lands in that directory's own project scope
under the one home, the user mount is shared, `neosian status` is green
from each, and one more run with `--url` moves the whole machine behind the
state process."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian._cli.setup import run_setup
from neosian._cli.status import collect
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope, user_scope
from neosian._foundation.memory.settings import CLIENT_TOKEN_ENV
from neosian._foundation.record.cli import run as record
from neosian._foundation.record.targets import installed_argv, resolve_target
from neosian._foundation.server.app import build_app
from neosian._foundation.shared.client_config import Environment
from tests.unit.record.payloads import prompt, session_start, stop, tool

_SESSIONS = {
    "proj-a": "aaaaaaaa-0000-4000-8000-000000000001",
    "proj-b": "bbbbbbbb-0000-4000-8000-000000000002",
}


def _claude(
    argv: Sequence[str], env: Mapping[str, str]
) -> tuple[int, str]:  # noqa: ARG001
    """Claude Code's own CLI, faked: `mcp add-json` writes its user scope."""
    if argv[:3] == ["claude", "mcp", "add-json"]:
        home = Path(env["HOME"])
        (home / ".claude.json").write_text(
            json.dumps({"mcpServers": {argv[-2]: json.loads(argv[-1])}})
        )
    return 0, ""


class TestOneMachineEveryProject:
    async def test_registered_once_every_project_is_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        projects = {name: tmp_path / name for name in _SESSIONS}
        for directory in projects.values():
            directory.mkdir()
        monkeypatch.chdir(tmp_path)  # never a project: the anchor must do the work
        env = {"NEOSIAN_HOME": str(tmp_path / "nh")}
        context = Environment(
            home=home,
            cwd=projects["proj-a"],
            platform="darwin",
            env={"HOME": str(home)},
            executable=sys.executable,
        )

        def setup(*argv: str, env: Mapping[str, str]) -> dict[str, Any]:
            out = io.StringIO()
            code = run_setup(
                [*argv, "--write", "--json"],
                env,
                context=context,
                out=out,
                err=io.StringIO(),
                runner=_claude,
            )
            assert code == 0, out.getvalue()
            payload: dict[str, Any] = json.loads(out.getvalue())
            return payload

        async def span(argv: list[str], project: str, text: str) -> None:
            session = _SESSIONS[project]
            for payload in (
                prompt(text, session=session),
                tool(session=session),
                stop("done", session=session),
            ):
                code = await record(
                    [*argv, "--project", str(projects[project])],
                    span_env,
                    stdin=io.StringIO(json.dumps(payload)),
                    out=io.StringIO(),
                    err=io.StringIO(),
                )
                assert code == 0

        # --- once: the client's own files, nothing per project
        setup(env=env)
        hooks = resolve_target("claude-code", context, "user")
        line = installed_argv(hooks)
        assert line is not None and "--mount" not in line
        assert line[-2:] == ["--project", "$CLAUDE_PROJECT_DIR"]
        assert all(list(directory.iterdir()) == [] for directory in projects.values())

        # --- every project: its own scope under the one home, /user shared
        argv, span_env = line[3:-2], env  # the verb's argv, as the file carries it
        store = FileStore(tmp_path / "nh")
        await store.write(
            str(user_scope()), "prefs", "tabs, never spaces", actor="cli:t"
        )
        for name in projects:
            await span(argv, name, f"hello from {name}")
        for name, directory in projects.items():
            mine, theirs = _SESSIONS[name], _SESSIONS[_other(name)]
            scope = str(project_scope(directory))
            assert await store.read(scope, f"sessions/{mine}") is not None
            assert await store.read(scope, f"sessions/{theirs}") is None
            out = io.StringIO()
            code = await record(
                [*argv, "--project", str(directory)],
                env,
                stdin=io.StringIO(json.dumps(session_start(session="fresh"))),
                out=out,
                err=io.StringIO(),
            )
            context_text = out.getvalue()
            assert code == 0 and "/user/prefs" in context_text  # shared
            assert f"hello from {name}" in context_text  # where THIS project left off
            assert f"hello from {_other(name)}" not in context_text

        # --- status: green from each, no file in either
        for name, directory in projects.items():
            status = await collect(replace(context, cwd=directory), env)
            claude = status.clients[0]
            assert claude.installed and claude.mcp_registered and claude.hooks_present
            assert claude.level == "user" and status.double_fire == ()
            assert status.last_session is not None
            assert status.last_session["conversation"] == _SESSIONS[name]

        # --- the one-writer answer in one command: the machine behind the daemon
        import neosian._foundation.server.remote as remote_module

        app = await build_app(store, token="t")
        connect = remote_module.RemoteStore.connect

        async def through_the_app(
            url: str, *, token: str, **_: Any
        ) -> remote_module.RemoteStore:
            return await connect(
                url, token=token, transport=httpx.ASGITransport(app=app)
            )

        monkeypatch.setattr(remote_module.RemoteStore, "connect", through_the_app)
        daemon_env = {**env, CLIENT_TOKEN_ENV: "t"}
        setup("--url", "http://state-process", env=daemon_env)
        line = installed_argv(hooks)
        assert line is not None and "--root" not in line
        assert line[line.index("--url") + 1] == "http://state-process"
        argv, span_env = line[3:-2], daemon_env
        for name in projects:
            await span(argv, name, f"again from {name}")
        for name, directory in projects.items():
            turns = await store.read_turns(_SESSIONS[name])
            assert [t.turn for t in turns] == [1, 2]
            assert (turns[1].actor or "").startswith("client:default/claude-code:")
            rows = await store.versions(
                str(project_scope(directory)), f"sessions/{_SESSIONS[name]}"
            )
            assert len(rows) == 2  # the document, through the wire, in its own scope


def _other(name: str) -> str:
    return next(other for other in _SESSIONS if other != name)
