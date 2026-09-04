"""The home (DESIGN §22, NU): one place for every project and agent.

Zero keys. `home()` reads the environment it is given; `project_scope`
spells the working directory in the scope grammar; both stay pure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neosian import home, project_mounts, project_scope
from neosian._foundation.memory.home import (
    HOME_DIR_NAME,
    HOME_ENV,
    PROJECT_MOUNT_PATH,
    USER_MOUNT_PATH,
    user_scope,
)
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.shared.exceptions import ConfigurationError


@pytest.mark.unit
class TestHome:
    def test_the_default_is_dot_neosian_under_the_user_home(self) -> None:
        assert home({}) == Path.home() / HOME_DIR_NAME

    def test_the_env_override_wins_and_expands_a_tilde(self, tmp_path: Path) -> None:
        assert home({HOME_ENV: str(tmp_path)}) == tmp_path
        assert home({HOME_ENV: "~/elsewhere"}) == Path.home() / "elsewhere"

    def test_an_empty_override_is_unset(self) -> None:
        # An absent CI value arrives as "" — falsiness, not None (§10).
        assert home({HOME_ENV: ""}) == Path.home() / HOME_DIR_NAME

    def test_the_process_environment_is_the_default_mapping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(HOME_ENV, str(tmp_path / "h"))
        assert home() == tmp_path / "h"

    def test_nothing_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / "never"
        home({HOME_ENV: str(target)})
        assert not target.exists()


@pytest.mark.unit
class TestProjectScope:
    @pytest.mark.parametrize(
        ("directory", "slug"),
        [
            ("neosian", "neosian"),
            ("my proj", "my-proj"),
            ("café λ", "caf"),  # the grammar's characters; edges trimmed
            ("..hidden.", "hidden"),
            ("a" * 200, "a" * 128),
        ],
    )
    def test_the_slug_is_the_directory_name_in_the_grammar(
        self, tmp_path: Path, directory: str, slug: str
    ) -> None:
        cwd = tmp_path / directory
        cwd.mkdir()
        scope = project_scope(cwd, login="ada")
        assert scope == f"user:ada/proj:{slug}"
        assert parse_scope(scope) == scope

    def test_the_login_is_read_from_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOGNAME", "Ada Lovelace")
        monkeypatch.setenv("USER", "Ada Lovelace")
        assert user_scope() == "user:Ada-Lovelace"
        assert project_scope(tmp_path / "p").startswith("user:Ada-Lovelace/proj:")

    def test_the_working_directory_is_the_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cwd = tmp_path / "demo-proj"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        assert project_scope(login="ada") == "user:ada/proj:demo-proj"

    def test_a_directory_without_a_name_has_no_scope(self) -> None:
        with pytest.raises(ConfigurationError, match="--scope"):
            project_scope(Path("/"), login="ada")

    def test_a_directory_that_slugs_to_nothing_has_no_scope(
        self, tmp_path: Path
    ) -> None:
        cwd = tmp_path / "..."
        cwd.mkdir()
        with pytest.raises(ConfigurationError, match="--scope"):
            project_scope(cwd, login="ada")


@pytest.mark.unit
class TestProjectMounts:
    def test_the_canonical_two_mount_layout(self, tmp_path: Path) -> None:
        cwd = tmp_path / "demo"
        cwd.mkdir()
        user, project = project_mounts(cwd, login="ada")
        assert (user.scope, user.mount_path) == ("user:ada", USER_MOUNT_PATH)
        assert (project.scope, project.mount_path) == (
            "user:ada/proj:demo",
            PROJECT_MOUNT_PATH,
        )
        assert not user.read_only and not project.read_only
        assert not user.edit_only and not project.edit_only
        assert user.description and project.description  # model-facing prose


@pytest.mark.unit
class TestBothDoors:
    """The phase's done-when, keylessly: on a fresh machine with no store
    flags a foreign agent's hooks and a neosian Conversation in the same
    directory land in one place, one scope — and one audit lists both,
    on the files and through the state process started with no flags."""

    async def test_two_agents_one_place_one_ledger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io
        import sys

        import httpx

        from neosian import AgentConfig, Conversation, FileStore, Model
        from neosian._foundation.llm.base import ToolCall
        from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
        from neosian._foundation.memory.audit import audit
        from neosian._foundation.memory.settings import StreamParser
        from neosian._foundation.record.cli import run as record
        from neosian._foundation.record.install import build_argv
        from neosian._foundation.record.settings import (
            add_record_arguments,
            resolve_record_settings,
        )
        from neosian._foundation.server.app import build_app
        from neosian._foundation.server.remote import RemoteStore
        from neosian._foundation.server.settings import (
            SERVE_TOKEN_ENV,
            parse_args as parse_serve,
        )
        from neosian._foundation.shared.types import ToolCallId, ToolName
        from tests.unit.record.payloads import SESSION, prompt, stop, tool

        fresh = tmp_path / "nh"
        project = tmp_path / "demo-proj"
        project.mkdir()
        monkeypatch.chdir(project)
        env = {HOME_ENV: str(fresh)}
        scope = project_scope(project)

        # Door two: the hook line the installer renders here, no store flags.
        parser = StreamParser(prog="neosian record install")
        add_record_arguments(parser)
        settings = resolve_record_settings(
            parser, parser.parse_args([]), env, layout=project
        )
        argv = build_argv(settings, executable=sys.executable)[3:]
        for payload in (prompt("hello"), tool(), stop("done")):
            out, err = io.StringIO(), io.StringIO()
            code = await record(
                argv, env, stdin=io.StringIO(json.dumps(payload)), out=out, err=err
            )
            assert code == 0, err.getvalue()

        # Door one: a neosian Conversation on the helpers, the same directory.
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("memory"),
                            arguments={
                                "command": "create",
                                "path": "/memories/colour",
                                "content": "teal",
                            },
                        ),
                    )
                ),
                FakeTurn(content="noted"),
            )
        )
        config = AgentConfig(
            system_prompt="remember",
            model=Model.FAKE,
            enable_todo=False,
            client_factory=lambda _: FakeClient(script),
        )
        store = FileStore(home(env))
        convo = Conversation(
            config, store=store, conversation_id="neo-1", memory_scope=scope
        )
        async with convo:
            await convo.send("my colour is teal")

        # One command lists both agents' work in the project scope.
        def actors(entries: object) -> set[str]:
            return {str(e.actor).split("#")[0] for e in entries}  # type: ignore[attr-defined]

        entries = await audit(store, scope)
        assert actors(entries) == {f"claude-code:{SESSION}", "conv:neo-1"}
        assert {e.path for e in entries} == {f"sessions/{SESSION}", "colour"}
        assert fresh.is_dir() and (fresh / "spool").is_dir()

        # The same through the state process started with no flags.
        serve = parse_serve([], {**env, SERVE_TOKEN_ENV: "t"})
        assert serve.store.root == fresh
        app = await build_app(FileStore(serve.store.root), token="t")
        remote = await RemoteStore.connect(
            "http://state-process", token="t", transport=httpx.ASGITransport(app=app)
        )
        async with remote:
            assert actors(await audit(remote, scope)) == actors(entries)
