"""`neosian serve` in-process: `main([...])`, never a real socket.

The grammar tier is extra-free by construction — the runner import is
lazy, so every exit-2 path answers without starlette or uvicorn, and a
missing extra surfaces as the install hint at exit 1 (§14.1's tiers).
"""

from pathlib import Path

import pytest

import neosian.server.serve as serve_module
from neosian._foundation.server.settings import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SERVE_TOKEN_ENV,
    parse_args,
)
from neosian.server.serve import main


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEOSIAN_POSTGRES_DSN", raising=False)
    monkeypatch.setenv(SERVE_TOKEN_ENV, "entry-token")


class _Captured:
    settings: object = None


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    holder = _Captured()

    async def fake_serve(settings: object) -> None:
        holder.settings = settings

    monkeypatch.setattr(serve_module, "_serve", fake_serve)
    return holder


class TestHappyPath:
    def test_filestore_serving(self, tmp_path: Path, captured: _Captured) -> None:
        root = tmp_path / "mem"
        assert main(["--root", str(root), "--scope", "user:demo"]) == 0
        settings = captured.settings
        assert settings is not None
        assert settings.store.root == root  # type: ignore[attr-defined]
        assert settings.token == "entry-token"  # type: ignore[attr-defined]
        assert settings.host == DEFAULT_HOST  # type: ignore[attr-defined]
        assert settings.port == DEFAULT_PORT  # type: ignore[attr-defined]

    def test_mounts_are_optional(self, tmp_path: Path, captured: _Captured) -> None:
        # §18's relaxation: the store API needs no mounts; only /mcp does.
        assert main(["--root", str(tmp_path / "mem")]) == 0
        settings = captured.settings
        assert settings.store.mounts == ()  # type: ignore[attr-defined]

    def test_no_flags_serves_the_home(
        self, tmp_path: Path, captured: _Captured, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DESIGN §22: `neosian serve` with no flags serves ~/.neosian
        # ($NEOSIAN_HOME) — the many-projects shape.
        monkeypatch.setenv("NEOSIAN_HOME", str(tmp_path / "home"))
        assert main([]) == 0
        settings = captured.settings
        assert settings.store.root == tmp_path / "home"  # type: ignore[attr-defined]


class TestGrammarTier:
    def test_a_missing_token_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(SERVE_TOKEN_ENV, raising=False)
        with pytest.raises(SystemExit) as excinfo:
            main(["--root", str(tmp_path / "mem")])
        assert excinfo.value.code == 2

    def test_two_stores_exit_2(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--root", "m", "--url", "http://x", "--scope", "user:demo"])
        assert excinfo.value.code == 2

    def test_root_and_dsn_conflict_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEOSIAN_POSTGRES_DSN", "postgresql://localhost/x")
        with pytest.raises(SystemExit) as excinfo:
            main(["--root", str(tmp_path / "mem")])
        assert excinfo.value.code == 2

    def test_a_bad_port_exits_2(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--root", str(tmp_path / "mem"), "--port", "70000"])
        assert excinfo.value.code == 2

    def test_a_bad_scope_exits_2_with_the_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--root", str(tmp_path / "m"), "--scope", "not a scope"])
        assert code == 2
        assert "[memory_scope_invalid]" in capsys.readouterr().err

    def test_nothing_is_constructed_on_the_grammar_tier(self, tmp_path: Path) -> None:
        root = tmp_path / "never"
        with pytest.raises(SystemExit):
            main(["--root", str(root), "--port", "0"])
        assert not root.exists()


class TestExtraAndInterrupt:
    def test_missing_extra_exits_1_with_the_hint(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def no_extra(settings: object) -> None:  # noqa: ARG001 - fake
            raise ImportError("uv add 'neosian[server]'")

        monkeypatch.setattr(serve_module, "_serve", no_extra)
        assert main(["--root", str(tmp_path / "m")]) == 1
        assert "neosian[server]" in capsys.readouterr().err

    def test_keyboard_interrupt_exits_130(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def interrupted(settings: object) -> None:  # noqa: ARG001 - fake
            raise KeyboardInterrupt

        monkeypatch.setattr(serve_module, "_serve", interrupted)
        assert main(["--root", str(tmp_path / "m")]) == 130


class TestSettings:
    def test_the_token_never_rides_argv(self, tmp_path: Path) -> None:
        # There is no --token flag: argv is world-readable in `ps`
        # (ledger #53's rule, applied to the bearer token).
        with pytest.raises(SystemExit):
            parse_args(
                ["--root", str(tmp_path), "--token", "t"],
                {SERVE_TOKEN_ENV: "x"},
            )

    def test_host_and_port_are_flags(self, tmp_path: Path) -> None:
        settings = parse_args(
            [
                "--root",
                str(tmp_path),
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
            ],  # noqa: S104
            {SERVE_TOKEN_ENV: "t"},
        )
        assert (settings.host, settings.port) == ("0.0.0.0", 9000)  # noqa: S104
