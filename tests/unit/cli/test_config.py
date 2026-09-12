"""The shell's config file under the home (DESIGN §30): `NEOSIAN_HOME`
moves it (the root conftest points every test at a temporary home),
keys are born private, sections beside the credentials round-trip."""

import stat
from pathlib import Path

import pytest

from neosian._cli.config import (
    ConfigFileError,
    config_exists,
    delete_api_key,
    delete_config,
    get_all_credentials,
    get_api_key,
    get_config_path,
    get_section,
    set_api_key,
    set_value,
)


class TestThePath:
    def test_lives_under_the_home(self, tmp_path: Path) -> None:
        assert get_config_path() == tmp_path / "home" / "config.toml"

    def test_the_home_override_moves_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NEOSIAN_HOME", str(tmp_path / "elsewhere"))
        assert get_config_path() == tmp_path / "elsewhere" / "config.toml"


class TestKeys:
    def test_absent_file_reads_as_nothing(self) -> None:
        assert config_exists() is False
        assert get_api_key("cerebras_api_key") is None
        assert get_all_credentials() == {}

    def test_set_get_update_and_keep_the_others(self) -> None:
        set_api_key("cerebras_api_key", "gsk_test")
        set_api_key("openai_api_key", "sk_test")
        set_api_key("cerebras_api_key", "gsk_new")
        assert get_api_key("cerebras_api_key") == "gsk_new"
        assert get_all_credentials() == {
            "cerebras_api_key": "gsk_new",
            "openai_api_key": "sk_test",
        }

    def test_delete_one_key(self) -> None:
        set_api_key("openai_api_key", "sk_test")
        assert delete_api_key("openai_api_key") is True
        assert delete_api_key("openai_api_key") is False
        assert get_all_credentials() == {}

    def test_delete_the_file(self) -> None:
        assert delete_config() is False
        set_api_key("openai_api_key", "sk_test")
        assert delete_config() is True
        assert config_exists() is False


class TestSections:
    def test_other_sections_ride_beside_the_credentials(self) -> None:
        set_api_key("openai_api_key", "sk_test")
        set_value("chat", "model", "gpt-5.6-sol")
        set_value("update", "mode", "notify")
        assert get_section("chat") == {"model": "gpt-5.6-sol"}
        assert get_section("update") == {"mode": "notify"}
        assert get_section("nothing") == {}
        assert get_api_key("openai_api_key") == "sk_test"


class TestPermissions:
    def test_new_file_and_directory_are_private(self) -> None:
        set_api_key("openai_api_key", "sk-test")
        path = get_config_path()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_an_existing_world_readable_file_is_tightened(self) -> None:
        path = get_config_path()
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        path.write_text("")
        path.chmod(0o644)
        set_api_key("openai_api_key", "sk-test")
        assert get_api_key("openai_api_key") == "sk-test"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


class TestABrokenFile:
    """EC-11: a file that is not TOML is named with its fix, never a
    traceback from `tomllib`."""

    def test_names_the_file_and_the_way_out(self) -> None:
        path = get_config_path()
        path.parent.mkdir(parents=True)
        path.write_text("[credentials\nopenai_api_key = 'sk'\n")
        with pytest.raises(ConfigFileError) as info:
            get_api_key("openai_api_key")
        assert str(path) in info.value.message
        assert "neosian configure --delete" in info.value.message
        assert info.value.__cause__ is None

    def test_delete_is_the_way_out(self) -> None:
        path = get_config_path()
        path.parent.mkdir(parents=True)
        path.write_text("not = = toml\n")
        assert delete_config() is True
        assert get_all_credentials() == {}
