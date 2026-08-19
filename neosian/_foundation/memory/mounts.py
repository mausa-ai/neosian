"""Mounts — the tool layer's virtual path space (DESIGN §8, C7).

A mount binds an opaque scope to a top-level directory name in the memory
tool's path space. The store never sees mounts: resolution and read-only
enforcement happen here, and `writable` is the library's only raise site
for `MemoryReadOnlyMountError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.scope import parse_scope
from neosian._foundation.shared.exceptions import (
    MemoryPathInvalidError,
    MemoryReadOnlyMountError,
)

if TYPE_CHECKING:
    from neosian._foundation.memory.base import MemoryStore


@dataclass(frozen=True, slots=True)
class Mount:
    """One binding of a scope to a top-level directory of the path space.

    `scope` is opaque (ECOSYSTEM §2); `mount_path` is a single document-path
    segment and becomes the directory the model sees (`/user`, `/project`).
    `description` is model-facing prose rendered into the memory index.
    """

    scope: str
    mount_path: str
    read_only: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        parse_scope(self.scope)
        validate_document_path(self.mount_path)
        if "/" in self.mount_path:
            raise MemoryPathInvalidError(
                self.mount_path, "a mount path is a single segment"
            )


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """The memory wiring for one agent: a store plus explicit mounts.

    Requiring at least one explicit mount is the "memory needs an explicit
    scope" constraint made structural — there is no silent isolation
    decision to make.
    """

    store: MemoryStore
    mounts: tuple[Mount, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "mounts", tuple(self.mounts))
        if not self.mounts:
            raise ValueError("MemoryConfig requires at least one mount")
        paths = [mount.mount_path for mount in self.mounts]
        if len(set(paths)) != len(paths):
            raise ValueError("mount paths must be unique")


def resolve(config: MemoryConfig, path: str) -> tuple[Mount, str]:
    """Resolve a virtual tool path to (mount, store document path).

    Leading and trailing slashes are ignored; the first segment selects the
    mount; the remainder — possibly empty, meaning the mount root — is the
    store's document path. The root itself (`/`) resolves to no single
    mount and raises.
    """
    stripped = path.strip("/")
    if not stripped:
        raise MemoryPathInvalidError(path, "the root is not a single mount")
    first, _, rest = stripped.partition("/")
    for mount in config.mounts:
        if mount.mount_path == first:
            return mount, rest
    known = ", ".join(f"/{mount.mount_path}" for mount in config.mounts)
    raise MemoryPathInvalidError(
        path, f"no mount named {first!r} (available mounts: {known})"
    )


def writable(mount: Mount) -> None:
    """Raise `MemoryReadOnlyMountError` when the mount refuses writes."""
    if mount.read_only:
        raise MemoryReadOnlyMountError(mount.mount_path)
