"""Muse's shared settings document and credential pass-through (DESIGN §22.6).

Both installers edit this file. Credentials remain environment references,
never values. Readers resolve managed hooks without repairing configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from neosian._foundation.memory.settings import (
    CLIENT_TOKEN_ENV,
    POSTGRES_DSN_ENV,
    StoreSettings,
)
from neosian._foundation.shared.client_config import (
    FIX_BY_HAND,
    Environment,
    InstallError,
    load_document,
)


def config_dir(context: Environment) -> Path:
    base = context.env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else context.home / ".config") / "muse"


def settings_document(path: Path) -> dict[str, Any]:
    document = load_document(path)
    if not path.exists():
        return {"schema_version": 1}
    version = document.get("schema_version")
    if type(version) is not int or version != 1:
        raise InstallError(f"{path}: expected schema_version 1", FIX_BY_HAND)
    return document


def credential_names(store: StoreSettings) -> tuple[str, ...]:
    if store.url is not None:
        return (CLIENT_TOKEN_ENV,)
    if store.dsn is not None:
        return (POSTGRES_DSN_ENV,)
    return ()


def managed_path(document: dict[str, Any], settings: Path) -> Path | None:
    value = document.get("managed_hooks_path")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else settings.parent / path
