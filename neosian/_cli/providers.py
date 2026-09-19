"""One provider table for the shell (DESIGN §30): the shipped providers
by their `EnvVars` name, every shipped door row and every registered
door by its `api_key_env` — so xAI and Gemini appear in `configure` and
`status` without a code change, and a door registered in an agent file
appears the moment the file is loaded. A key stored for a door the shell
has not loaded (`configure --env NAME`) is a row by its env name, and the
loader exports what is stored, not what the table knows: a door that
registers after it ran still finds its key.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Final

from neosian._cli.config import ConfigFileError, get_all_credentials
from neosian._foundation.shared.constants import EnvVars
from neosian._foundation.shared.registry import registered_models
from neosian._foundation.shared.types import Model, Provider


@dataclass(frozen=True, slots=True)
class ProviderKey:
    """One row: the provider's name and the environment variable its key
    lives in; `key` is the `config.toml` spelling of the same."""

    name: str
    env: str

    @property
    def key(self) -> str:
        return self.env.lower()


ENV_NAME: Final = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")  # a door's `api_key_env` grammar

_SHIPPED = (
    ProviderKey(Provider.OPENAI.value, EnvVars.OPENAI_API_KEY),
    ProviderKey(Provider.ANTHROPIC.value, EnvVars.ANTHROPIC_API_KEY),
    ProviderKey(Provider.CEREBRAS.value, EnvVars.CEREBRAS_API_KEY),
)


def provider_keys() -> tuple[ProviderKey, ...]:
    """The table: the three shipped providers, then the shipped door rows
    (one per door, in enum order), then registered doors in registration
    order, then any other key the file holds, a row by its env name."""
    rows = list(_SHIPPED)
    names = {row.name for row in rows}
    doors = [m.door for m in Model if m.door is not None]
    doors += [m.door for m in registered_models()]
    for door in doors:
        if door.name not in names:
            names.add(door.name)
            rows.append(ProviderKey(door.name, door.api_key_env))
    tabled = {row.env for row in rows}
    rows += [ProviderKey(env, env) for env in _stored_envs() if env not in tabled]
    return tuple(rows)


def _stored_envs() -> list[str]:
    """The env names the file holds keys for; none from a broken file
    (`status` reports that as its own finding)."""
    try:
        stored = get_all_credentials()
    except ConfigFileError:
        return []
    return [key.upper() for key in stored if ENV_NAME.match(key.upper())]


def find_provider(name: str) -> ProviderKey | None:
    return next((row for row in provider_keys() if row.name == name), None)


def key_source(row: ProviderKey, env: Mapping[str, str]) -> str | None:
    """Where a provider's key comes from — `env` wins over `file` — or None;
    never the value."""
    if env.get(row.env):
        return "env"
    if get_all_credentials().get(row.key):
        return "file"
    return None


def load_keys_into_env(environ: MutableMapping[str, str] | None = None) -> None:
    """Keys from the file into the environment where it has none — the
    shell's job before any provider client is built. By what is stored,
    never by the table: the agent or eval file that registers a door loads
    after this runs, and its key must already be there."""
    target = os.environ if environ is None else environ
    for key, stored in get_all_credentials().items():
        env = key.upper()
        if stored and ENV_NAME.match(env) and not target.get(env):
            target[env] = stored
