"""`python -m neosian.mcp` in-process: `main([...])`, never the real
stdio transport (it claims the test process's fd 0/1)."""

from pathlib import Path

import pytest

import neosian.mcp.serve as serve_module
from neosian._foundation.memory.file import FileStore
from neosian.mcp.serve import main


class _Captured:
    server: object = None


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    holder = _Captured()

    async def fake_serve_stdio(server: object) -> None:
        holder.server = server

    monkeypatch.setattr(serve_module, "serve_stdio", fake_serve_stdio)
    return holder


class TestHappyPath:
    def test_filestore_serving(
        self, tmp_path: Path, captured: _Captured, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)
        root = tmp_path / "mem"
        code = main(["--root", str(root), "--scope", "user:demo"])
        assert code == 0
        assert root.is_dir()  # the FileStore was really constructed
        server = captured.server
        assert server is not None
        assert "memories" in str(server.instructions)  # type: ignore[attr-defined]


class TestErrorPaths:
    def test_grammar_error_exits_2(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--root", "m", "--url", "http://x", "--scope", "user:demo"])
        assert excinfo.value.code == 2

    def test_bad_scope_exits_2_with_the_code(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)
        code = main(["--root", str(tmp_path / "m"), "--scope", "not a scope"])
        assert code == 2
        assert "[memory_scope_invalid]" in capsys.readouterr().err

    def test_keyboard_interrupt_exits_130(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)

        async def interrupted(server: object) -> None:  # noqa: ARG001 - fake
            raise KeyboardInterrupt

        monkeypatch.setattr(serve_module, "serve_stdio", interrupted)
        code = main(["--root", str(tmp_path / "m"), "--scope", "user:demo"])
        assert code == 130

    def test_missing_sdk_exits_1_with_the_hint(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)

        async def no_sdk(
            config: object,  # noqa: ARG001 - fake
            *,
            actor: object = None,  # noqa: ARG001 - fake
            conversations: object = None,  # noqa: ARG001 - fake
        ) -> object:
            raise ImportError("uv add 'neosian[mcp]'")

        monkeypatch.setattr(serve_module, "create_memory_server", no_sdk)
        code = main(["--root", str(tmp_path / "m"), "--scope", "user:demo"])
        assert code == 1
        assert "neosian[mcp]" in capsys.readouterr().err


class TestStoreSelection:
    def test_dsn_selects_postgres_and_closes_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured: _Captured,  # noqa: ARG002 - the fixture patches serve_stdio
    ) -> None:
        closed: list[bool] = []

        class FakePostgres:
            def __init__(self, dsn: str, *, schema: str) -> None:
                self.dsn = dsn
                self.schema = schema

            async def aclose(self) -> None:
                closed.append(True)

        async def fake_create(
            config: object,  # noqa: ARG001 - fake
            *,
            actor: object = None,  # noqa: ARG001 - fake
            conversations: object = None,  # noqa: ARG001 - fake
        ) -> object:
            return object()

        # The entry rides `open_store` (NL), whose function-local import
        # resolves the class off the postgres module at call time.
        import neosian._foundation.postgres.store as postgres_module

        monkeypatch.setattr(postgres_module, "PostgresStore", FakePostgres)
        monkeypatch.setattr(serve_module, "create_memory_server", fake_create)
        monkeypatch.setenv("NEOSIAN_POSTGRES_DSN", "postgresql://localhost/x")
        code = main(["--scope", "user:demo", "--schema", "acme"])
        assert code == 0
        assert closed == [True]

    def test_filestore_is_not_closed(
        self,
        tmp_path: Path,
        captured: _Captured,  # noqa: ARG002 - the fixture patches serve_stdio
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # FileStore has no aclose; the finally branch must not touch it.
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)
        assert not hasattr(FileStore, "aclose")
        code = main(["--root", str(tmp_path / "m"), "--scope", "user:demo"])
        assert code == 0


class TestInstallRouting:
    """`install` is one literal first token, routed before the server grammar."""

    def test_install_routes_before_parse_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import neosian._foundation.mcp.install as install_module

        seen: list[list[str]] = []

        def fake_run_install(
            argv: object,
            env: object,  # noqa: ARG001 - fake
            **kwargs: object,  # noqa: ARG001 - fake
        ) -> int:
            assert isinstance(argv, list)
            seen.append(argv)
            return 0

        def exploding_parse_args(
            *args: object,  # noqa: ARG001 - fake
            **kwargs: object,  # noqa: ARG001 - fake
        ) -> object:
            raise AssertionError("the server grammar must not see install argv")

        monkeypatch.setattr(install_module, "run_install", fake_run_install)
        monkeypatch.setattr(serve_module, "parse_args", exploding_parse_args)
        code = main(["install", "--client", "cursor"])
        assert code == 0
        assert seen == [["--client", "cursor"]]

    def test_install_help_comes_from_installs_own_grammar(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The typer pass-through strips no --help (help_option_names=[]);
        # install's own argparse must answer it at exit 0.
        code = main(["install", "--help"])
        assert code == 0
        out = capsys.readouterr().out
        assert "--client" in out
        assert "--write" in out
        assert "neosian mcp install" in out  # the threaded prog

    def test_a_flag_valued_install_is_not_intercepted(
        self,
        tmp_path: Path,
        captured: _Captured,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Only argv[0] routes: "install" elsewhere still serves.
        monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)
        code = main(["--root", str(tmp_path / "install"), "--scope", "user:demo"])
        assert code == 0
        assert captured.server is not None
