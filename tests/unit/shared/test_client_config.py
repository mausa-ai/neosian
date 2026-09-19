"""The installers' shared half (DESIGN §14.5, §22.6): where each client
keeps its config, the level flag, and the reader that never repairs."""

import argparse
import sys
from pathlib import Path

import pytest

from neosian._foundation.shared.client_config import (
    LEVELS,
    Environment,
    add_level_argument,
    claude_home,
    codex_home,
    opencode_config_dir,
    read_document,
)


def _context(tmp_path: Path, env: dict[str, str] | None = None) -> Environment:
    return Environment(
        home=tmp_path,
        cwd=tmp_path / "proj",
        platform="darwin",
        env=env or {},
        executable=sys.executable,
    )


class TestTheConfigHomes:
    def test_the_defaults_sit_under_the_home(self, tmp_path: Path) -> None:
        context = _context(tmp_path)
        assert claude_home(context) == tmp_path / ".claude"
        assert codex_home(context) == tmp_path / ".codex"
        assert opencode_config_dir(context) == tmp_path / ".config" / "opencode"

    @pytest.mark.parametrize(
        ("variable", "resolve"),
        [
            ("CLAUDE_CONFIG_DIR", claude_home),
            ("CODEX_HOME", codex_home),
            ("OPENCODE_CONFIG_DIR", opencode_config_dir),
        ],
    )
    def test_each_clients_own_variable_moves_it(
        self, tmp_path: Path, variable: str, resolve: object
    ) -> None:
        moved = _context(tmp_path, {variable: str(tmp_path / "elsewhere")})
        assert callable(resolve)
        assert resolve(moved) == tmp_path / "elsewhere"
        assert resolve(_context(tmp_path, {variable: ""})) != tmp_path / "elsewhere"


class TestTheLevel:
    def test_once_per_machine_is_the_default(self) -> None:
        parser = argparse.ArgumentParser()
        add_level_argument(parser)
        assert parser.parse_args([]).level == "user"
        assert parser.parse_args(["--level", "project"]).level == "project"
        assert LEVELS == ("user", "project")

    def test_an_unknown_level_is_grammar(self) -> None:
        parser = argparse.ArgumentParser()
        add_level_argument(parser)
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["--level", "team"])
        assert excinfo.value.code == 2


class TestReadDocument:
    def test_json_and_toml_by_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text('{"mcpServers": {}}')
        (tmp_path / "b.toml").write_text('[mcp_servers.x]\ncommand = "c"\n')
        assert read_document(tmp_path / "a.json") == {"mcpServers": {}}
        toml = read_document(tmp_path / "b.toml")
        assert toml is not None and toml["mcp_servers"]["x"]["command"] == "c"

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", ""])
    def test_what_is_not_a_document_is_none(self, tmp_path: Path, text: str) -> None:
        # A reader reports; it never repairs and never raises.
        (tmp_path / "bad.json").write_text(text)
        assert read_document(tmp_path / "bad.json") is None

    def test_a_missing_file_is_none(self, tmp_path: Path) -> None:
        assert read_document(tmp_path / "absent.json") is None
