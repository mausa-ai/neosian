"""One provider table for the shell (DESIGN §30): the shipped providers
by their `EnvVars` name, every shipped door row and every registered
door by its `api_key_env` — so xAI and Gemini appear in `configure`,
`status` and the key loader without a code change, and a door registered
in an agent file appears the moment the file is loaded.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from neosian._cli.config import get_all_credentials, get_api_key
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


_SHIPPED = (
    ProviderKey(Provider.OPENAI.value, EnvVars.OPENAI_API_KEY),
    ProviderKey(Provider.ANTHROPIC.value, EnvVars.ANTHROPIC_API_KEY),
    ProviderKey(Provider.CEREBRAS.value, EnvVars.CEREBRAS_API_KEY),
)


def provider_keys() -> tuple[ProviderKey, ...]:
    """The table: the three shipped providers, then the shipped door rows
    (one per door, in enum order), then registered doors in registration
    order."""
    rows = list(_SHIPPED)
    names = {row.name for row in rows}
    doors = [m.door for m in Model if m.door is not None]
    doors += [m.door for m in registered_models()]
    for door in doors:
        if door.name not in names:
            names.add(door.name)
            rows.append(ProviderKey(door.name, door.api_key_env))
    return tuple(rows)


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
    shell's job before any provider client is built."""
    target = os.environ if environ is None else environ
    for row in provider_keys():
        if not target.get(row.env):
            stored = get_api_key(row.key)
            if stored:
                target[row.env] = stored
