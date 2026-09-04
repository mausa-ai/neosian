"""The home (DESIGN §22, NU): one place for every project and agent.

`~/.neosian`, or `$NEOSIAN_HOME` — the store every argv entry point
defaults to when `--root`, `--url` and the DSN are all absent, the
playground's store, and the root a neosian agent lands in through
`home()`. Per project is a scope, not a root: `project_scope` spells the
canonical two-mount layout — `user:<login>` at `/user`,
`user:<login>/proj:<slug>` at `/project` — from the working directory.
The spelling is derived; the scope stays explicit wherever it is written
(the hook line, the registration, the call). Pure: nothing here creates
a directory.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.scope import Scope, parse_scope
from neosian._foundation.shared.exceptions import ConfigurationError

HOME_ENV: Final = "NEOSIAN_HOME"
HOME_DIR_NAME: Final = ".neosian"
SPOOL_DIR_NAME: Final = "spool"
USER_MOUNT_PATH: Final = "user"
PROJECT_MOUNT_PATH: Final = "project"
USER_KIND: Final = "user"
PROJECT_KIND: Final = "proj"
_ID_CHARS: Final = "_.-"
_ID_MAX: Final = 128
_FIX: Final = "pass --scope or --mount (or memory_scope=) to name the scope yourself"


def home(env: Mapping[str, str] | None = None) -> Path:
    """The home directory: `$NEOSIAN_HOME`, else `~/.neosian`.

    Reads `env` (the process environment when None) and never creates
    the directory — the store that opens it does, privately.
    """
    if env is None:
        env = os.environ
    override = env.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / HOME_DIR_NAME


def _slug(name: str) -> str:
    """A scope id from a name: the grammar's characters, the rest `-`."""
    slug = "".join(
        c if c.isascii() and (c.isalnum() or c in _ID_CHARS) else "-" for c in name
    )
    return slug[:_ID_MAX].strip("-.")


def _login() -> str:
    try:
        login = _slug(getpass.getuser())
    except OSError:  # no passwd entry and no LOGNAME/USER (a bare container)
        login = ""
    if not login:
        raise ConfigurationError(f"the login cannot be read as a scope id; {_FIX}")
    return login


def user_scope(*, login: str | None = None) -> Scope:
    """`user:<login>` — the login from the environment unless given."""
    return parse_scope(f"{USER_KIND}:{login if login is not None else _login()}")


def project_scope(cwd: Path | None = None, *, login: str | None = None) -> Scope:
    """`user:<login>/proj:<slug>` for the working directory.

    The slug is the directory's own name in the scope grammar; two
    same-named directories share a scope by design — name it yourself
    to separate them. A directory without a name (the filesystem root)
    has no derived scope.
    """
    directory = (cwd if cwd is not None else Path.cwd()).resolve()
    slug = _slug(directory.name)
    if not slug:
        raise ConfigurationError(
            f"no project name in {str(directory)!r} to derive a scope from; {_FIX}"
        )
    return parse_scope(f"{user_scope(login=login)}/{PROJECT_KIND}:{slug}")


def project_mounts(
    cwd: Path | None = None, *, login: str | None = None
) -> tuple[Mount, Mount]:
    """The canonical two-mount layout: the user's durable facts at
    `/user`, this project's at `/project` — both read-write."""
    return (
        Mount(
            scope=user_scope(login=login),
            mount_path=USER_MOUNT_PATH,
            description="durable facts about the user: preferences, identity, "
            "standing constraints",
        ),
        Mount(
            scope=project_scope(cwd, login=login),
            mount_path=PROJECT_MOUNT_PATH,
            description="facts local to this project: decisions, layout, "
            "conventions",
        ),
    )
