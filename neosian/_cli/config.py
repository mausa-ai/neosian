"""The shell's config file — `<home>/config.toml` (DESIGN §30).

One TOML document under the home (`~/.neosian`, or `NEOSIAN_HOME`):
`[credentials]` holds an API key per provider under the provider's
environment variable name lowercased (`openai_api_key`), `[chat]` the
resident agent's model, `[update]` the update knob. The file holds
secrets: it is born 0600 in a 0700 directory and both are tightened on
every write. The library reads keys from the environment only; loading
them from here is the shell's job (`providers.load_keys_into_env`).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Final

import tomli_w

from neosian._foundation.memory.home import home
from neosian._foundation.shared.fileio import PRIVATE_DIR, PRIVATE_FILE, private_mkdir

CONFIG_FILE_NAME: Final = "config.toml"
CREDENTIALS: Final = "credentials"


def get_config_path() -> Path:
    """`<home>/config.toml` — the home read from the process environment."""
    return home() / CONFIG_FILE_NAME


def read_config() -> dict[str, Any]:
    """The whole document, or `{}` when the file does not exist."""
    path = get_config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_config(config: dict[str, Any]) -> None:
    path = get_config_path()
    private_mkdir(path.parent)
    os.chmod(path.parent, PRIVATE_DIR)  # an existing directory too
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    os.fchmod(fd, PRIVATE_FILE)  # a file that already exists, before the key lands
    with os.fdopen(fd, "wb") as f:
        tomli_w.dump(config, f)


def get_section(name: str) -> dict[str, Any]:
    """One table of the document (`chat`, `update`, …), `{}` when absent."""
    section = read_config().get(name, {})
    return dict(section) if isinstance(section, dict) else {}


def set_value(section: str, key: str, value: str) -> None:
    config = read_config()
    table = config.get(section)
    if not isinstance(table, dict):
        table = {}
    table[key] = value
    config[section] = table
    write_config(config)


def get_api_key(key_name: str) -> str | None:
    value = get_section(CREDENTIALS).get(key_name)
    return value if isinstance(value, str) else None


def set_api_key(key_name: str, value: str) -> None:
    set_value(CREDENTIALS, key_name, value)


def delete_api_key(key_name: str) -> bool:
    """Drop one credential; True when it was there."""
    config = read_config()
    table = config.get(CREDENTIALS)
    if not isinstance(table, dict) or key_name not in table:
        return False
    del table[key_name]
    write_config(config)
    return True


def get_all_credentials() -> dict[str, str]:
    return {k: v for k, v in get_section(CREDENTIALS).items() if isinstance(v, str)}


def config_exists() -> bool:
    return get_config_path().exists()


def delete_config() -> bool:
    """Delete the file; True when there was one."""
    path = get_config_path()
    if path.exists():
        path.unlink()
        return True
    return False
