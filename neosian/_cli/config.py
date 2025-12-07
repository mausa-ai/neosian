"""Configuration file management.

Handles reading and writing credentials from ~/.neosian/config.toml.
"""

from pathlib import Path

from neosian._foundation.shared.constants import Config

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

import tomli_w


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

    Creates the directory if it doesn't exist.
    """
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def get_api_key(key_name: str) -> str | None:
    """Get an API key from the config file.

    Args:
        key_name: The key name (e.g., Config.GROQ_API_KEY).

    Returns:
        The API key or None if not found.
    """
    config = _read_config()
    credentials = config.get("credentials", {})
    return credentials.get(key_name)


def set_api_key(key_name: str, value: str) -> None:
    """Set an API key in the config file.

    Args:
        key_name: The key name (e.g., Config.GROQ_API_KEY).
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
