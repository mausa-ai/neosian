"""The entry point's grammar: pure parsing, structural validation."""

from pathlib import Path

import pytest

from neosian._foundation.mcp.settings import (
    POSTGRES_DSN_ENV,
    ServerSettings,
    parse_args,
)
from neosian._foundation.shared.exceptions import (
    MemoryPathInvalidError,
    MemoryScopeInvalidError,
)

_ENV: dict[str, str] = {}
_DSN_ENV = {POSTGRES_DSN_ENV: "postgresql://localhost/x"}


class TestStores:
    def test_root_builds_filestore_settings(self, tmp_path: Path) -> None:
        settings = parse_args(
            ["--root", str(tmp_path / "mem"), "--scope", "user:me"], _ENV
        )
        assert settings.root == tmp_path / "mem"
        assert settings.dsn is None
        # Parsing is pure: nothing was created.
        assert not (tmp_path / "mem").exists()

    def test_env_dsn_builds_postgres_settings(self) -> None:
        settings = parse_args(["--scope", "user:me"], _DSN_ENV)
        assert settings.dsn == "postgresql://localhost/x"
        assert settings.root is None
        assert settings.schema == "neosian"

    def test_schema_override(self) -> None:
        settings = parse_args(["--scope", "user:me", "--schema", "acme"], _DSN_ENV)
        assert settings.schema == "acme"

    def test_empty_env_dsn_is_unset(self) -> None:
        # An absent CI secret arrives as "" — falsiness, not None (§10).
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--scope", "user:me"], {POSTGRES_DSN_ENV: ""})
        assert excinfo.value.code == 2

    @pytest.mark.parametrize(
        "argv",
        [
            ["--scope", "user:me"],  # no store at all
            ["--root", "m", "--schema", "acme", "--scope", "user:me"],
        ],
    )
    def test_store_shape_errors_exit_2(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parse_args(argv, _ENV)
        assert excinfo.value.code == 2

    def test_root_and_dsn_conflict(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--root", "m", "--scope", "user:me"], _DSN_ENV)
        assert excinfo.value.code == 2


class TestMounts:
    def test_scope_is_the_memories_sugar(self) -> None:
        settings = parse_args(["--root", "m", "--scope", "user:me"], _ENV)
        (mount,) = settings.mounts
        assert mount.scope == "user:me"
        assert mount.mount_path == "memories"
        assert mount.read_only is False

    def test_mounts_are_repeatable(self) -> None:
        settings = parse_args(
            [
                "--root",
                "m",
                "--mount",
                "scope=user:me,path=memories",
                "--mount",
                "scope=tenant:acme/kb:main,path=kb,ro",
            ],
            _ENV,
        )
        assert [m.mount_path for m in settings.mounts] == ["memories", "kb"]
        assert [m.read_only for m in settings.mounts] == [False, True]

    def test_scope_and_mount_conflict(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parse_args(
                ["--root", "m", "--scope", "user:me", "--mount", "scope=a:b,path=p"],
                _ENV,
            )
        assert excinfo.value.code == 2

    def test_no_mounts_exits_2(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--root", "m"], _ENV)
        assert excinfo.value.code == 2

    @pytest.mark.parametrize(
        "token",
        [
            "scope=user:me",  # missing path
            "path=memories",  # missing scope
            "scope=user:me,path=m,rw",  # unknown flag
            "scope=user:me,scope=twice,path=m",  # duplicate key
        ],
    )
    def test_malformed_mount_exits_2(self, token: str) -> None:
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--root", "m", "--mount", token], _ENV)
        assert excinfo.value.code == 2

    def test_bad_scope_is_structural(self) -> None:
        with pytest.raises(MemoryScopeInvalidError):
            parse_args(["--root", "m", "--scope", "not a scope"], _ENV)

    def test_bad_mount_path_is_structural(self) -> None:
        with pytest.raises(MemoryPathInvalidError):
            parse_args(["--root", "m", "--mount", "scope=user:me,path=a/b"], _ENV)


class TestActor:
    def test_defaults_to_mcp(self) -> None:
        settings = parse_args(["--root", "m", "--scope", "user:me"], _ENV)
        assert settings.actor == "mcp"

    def test_override(self) -> None:
        settings = parse_args(
            ["--root", "m", "--scope", "user:me", "--actor", "mcp:claude-code"], _ENV
        )
        assert settings.actor == "mcp:claude-code"


class TestShape:
    def test_settings_are_frozen(self) -> None:
        settings = parse_args(["--root", "m", "--scope", "user:me"], _ENV)
        assert isinstance(settings, ServerSettings)
        with pytest.raises(AttributeError):
            settings.actor = "other"  # type: ignore[misc]
