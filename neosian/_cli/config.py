"""Configuration file management.

Handles reading and writing credentials from ~/.neosian/config.toml.
"""

import os
import tomllib
from pathlib import Path

import tomli_w

from neosian._foundation.shared.constants import Config
from neosian._foundation.shared.fileio import PRIVATE_DIR, PRIVATE_FILE, private_mkdir


def _get_config_path() -> Path:
    """Get the path to the config file."""
    return Path.home() / Config.DIR_NAME / Config.FILE_NAME


def _read_config() -> dict[str, dict[str, str]]:
    """Read the config file.

    Returns:
        Config dictionary or empty dict if file doesn't exist.
    """
    config_path = _get_config_path()
    if not config_path.exists():
        return {}

    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _write_config(config: dict[str, dict[str, str]]) -> None:
    """Write the config file.

    The file holds API keys: it is created 0600 in a 0700 directory, and
    both are tightened on every write when they already exist.
    """
    config_path = _get_config_path()
    private_mkdir(config_path.parent)
    os.chmod(config_path.parent, PRIVATE_DIR)  # an existing directory too

    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    os.fchmod(fd, PRIVATE_FILE)  # a file that already exists, before the key lands
    with os.fdopen(fd, "wb") as f:
        tomli_w.dump(config, f)


def get_api_key(key_name: str) -> str | None:
    """Get an API key from the config file.

    Args:
        key_name: The key name (e.g., Config.OPENAI_API_KEY).

    Returns:
        The API key or None if not found.
    """
    config = _read_config()
    credentials = config.get("credentials", {})
    return credentials.get(key_name)


def set_api_key(key_name: str, value: str) -> None:
    """Set an API key in the config file.

    Args:
        key_name: The key name (e.g., Config.OPENAI_API_KEY).
        value: The API key value.
    """
    config = _read_config()
    if "credentials" not in config:
        config["credentials"] = {}
    config["credentials"][key_name] = value
    _write_config(config)


def get_all_credentials() -> dict[str, str]:
    """Get all stored credentials.

    Returns:
        Dictionary of credential key names to values.
    """
    config = _read_config()
    return config.get("credentials", {})


def config_exists() -> bool:
    """Check if the config file exists."""
    return _get_config_path().exists()


def get_config_path() -> Path:
    """Get the path to the config file (public API)."""
    return _get_config_path()


def delete_config() -> bool:
    """Delete the config file.

    Returns:
        True if file was deleted, False if it didn't exist.
    """
    config_path = _get_config_path()
    if config_path.exists():
        config_path.unlink()
        return True
    return False
