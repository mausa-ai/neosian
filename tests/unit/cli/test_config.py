"""Unit tests for CLI config module."""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian._cli.config import (
    config_exists,
    delete_config,
    get_all_credentials,
    get_api_key,
    get_config_path,
    set_api_key,
)
from neosian._foundation.shared.constants import Config


class TestGetConfigPath:
    """Tests for get_config_path function."""

    def test_returns_path_in_home_directory(self) -> None:
        """Config path should be in user's home directory."""
        path = get_config_path()
        assert path.parent.name == Config.DIR_NAME
        assert path.name == Config.FILE_NAME

    def test_returns_path_object(self) -> None:
        """Should return a Path object."""
        path = get_config_path()
        assert isinstance(path, Path)


class TestConfigExists:
    """Tests for config_exists function."""

    def test_returns_false_when_no_config(self, tmp_path: Path) -> None:
        """Should return False when config file doesn't exist."""
        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = tmp_path / "nonexistent" / "config.toml"
            assert config_exists() is False

    def test_returns_true_when_config_exists(self, tmp_path: Path) -> None:
        """Should return True when config file exists."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            assert config_exists() is True


class TestGetApiKey:
    """Tests for get_api_key function."""

    def test_returns_none_when_no_config(self, tmp_path: Path) -> None:
        """Should return None when config file doesn't exist."""
        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = tmp_path / "nonexistent" / "config.toml"
            assert get_api_key(Config.CEREBRAS_API_KEY) is None

    def test_returns_none_when_key_not_set(self, tmp_path: Path) -> None:
        """Should return None when key is not in config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[credentials]\nopenai_api_key = "sk-test"')

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            assert get_api_key(Config.CEREBRAS_API_KEY) is None

    def test_returns_key_value(self, tmp_path: Path) -> None:
        """Should return the stored key value."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[credentials]\ncerebras_api_key = "gsk_testkey123"')

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            assert get_api_key(Config.CEREBRAS_API_KEY) == "gsk_testkey123"


class TestSetApiKey:
    """Tests for set_api_key function."""

    def test_creates_config_file(self, tmp_path: Path) -> None:
        """Should create config file if it doesn't exist."""
        config_dir = tmp_path / ".neosian"
        config_file = config_dir / "config.toml"

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            set_api_key(Config.CEREBRAS_API_KEY, "gsk_newkey")

        assert config_file.exists()

    def test_stores_key_value(self, tmp_path: Path) -> None:
        """Should store the key value in config."""
        config_dir = tmp_path / ".neosian"
        config_file = config_dir / "config.toml"

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            set_api_key(Config.CEREBRAS_API_KEY, "gsk_testkey")
            assert get_api_key(Config.CEREBRAS_API_KEY) == "gsk_testkey"

    def test_updates_existing_key(self, tmp_path: Path) -> None:
        """Should update existing key value."""
        config_dir = tmp_path / ".neosian"
        config_file = config_dir / "config.toml"

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            set_api_key(Config.CEREBRAS_API_KEY, "old_key")
            set_api_key(Config.CEREBRAS_API_KEY, "new_key")
            assert get_api_key(Config.CEREBRAS_API_KEY) == "new_key"

    def test_preserves_other_keys(self, tmp_path: Path) -> None:
        """Should preserve other keys when updating one."""
        config_dir = tmp_path / ".neosian"
        config_file = config_dir / "config.toml"

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            set_api_key(Config.CEREBRAS_API_KEY, "cerebras_key")
            set_api_key(Config.OPENAI_API_KEY, "openai_key")

            assert get_api_key(Config.CEREBRAS_API_KEY) == "cerebras_key"
            assert get_api_key(Config.OPENAI_API_KEY) == "openai_key"


class TestGetAllCredentials:
    """Tests for get_all_credentials function."""

    def test_returns_empty_dict_when_no_config(self, tmp_path: Path) -> None:
        """Should return empty dict when config doesn't exist."""
        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = tmp_path / "nonexistent" / "config.toml"
            assert get_all_credentials() == {}

    def test_returns_all_credentials(self, tmp_path: Path) -> None:
        """Should return all stored credentials."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[credentials]\ncerebras_api_key = "gsk_test"\nopenai_api_key = "sk_test"'
        )

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            credentials = get_all_credentials()

        assert credentials == {
            "cerebras_api_key": "gsk_test",
            "openai_api_key": "sk_test",
        }


class TestDeleteConfig:
    """Tests for delete_config function."""

    def test_returns_false_when_no_config(self, tmp_path: Path) -> None:
        """Should return False when config doesn't exist."""
        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = tmp_path / "nonexistent" / "config.toml"
            assert delete_config() is False

    def test_deletes_config_file(self, tmp_path: Path) -> None:
        """Should delete the config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[credentials]\ncerebras_api_key = "test"')

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            result = delete_config()

        assert result is True
        assert not config_file.exists()

    def test_returns_true_after_deletion(self, tmp_path: Path) -> None:
        """Should return True after successful deletion."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")

        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_file
            assert delete_config() is True


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _skip_unless_posix_modes() -> None:
    if os.name != "posix":
        pytest.skip("file modes are a POSIX contract")


class TestWritePermissions:
    """API keys are written 0600 in a 0700 directory (EC-2)."""

    def test_new_file_and_directory_are_private(self, tmp_path: Path) -> None:
        _skip_unless_posix_modes()
        config_file = tmp_path / "home" / ".neosian" / "config.toml"
        with patch("neosian._cli.config._get_config_path", return_value=config_file):
            set_api_key(Config.OPENAI_API_KEY, "sk-test")

        assert _mode(config_file) == 0o600
        assert _mode(config_file.parent) == 0o700

    def test_existing_world_readable_file_is_tightened(self, tmp_path: Path) -> None:
        _skip_unless_posix_modes()
        config_file = tmp_path / "config.toml"
        config_file.write_text("")
        config_file.chmod(0o644)
        with patch("neosian._cli.config._get_config_path", return_value=config_file):
            set_api_key(Config.OPENAI_API_KEY, "sk-test")
            assert get_api_key(Config.OPENAI_API_KEY) == "sk-test"

        assert _mode(config_file) == 0o600


@pytest.mark.unit
class TestPrivateConfigDirectory:
    """EC-2: the key file is 0600 in a 0700 directory — and a directory
    that already exists world-readable is tightened on the next write."""

    def test_tightens_an_existing_directory(self, tmp_path: Path) -> None:
        from neosian._cli.config import _write_config

        config_dir = tmp_path / ".neosian"
        config_dir.mkdir(mode=0o755)
        with patch("neosian._cli.config._get_config_path") as mock_path:
            mock_path.return_value = config_dir / "config.toml"
            _write_config({"credentials": {"openai": "sk-test"}})
        assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((config_dir / "config.toml").stat().st_mode) == 0o600
