"""The installation shape (DESIGN §30): one detection table shared by
`neosian status` and `neosian update`, and the exact upgrade line each
shape wants. Pure over the interpreter prefix and the environment."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CONTAINER_ENV: Final = "NEOSIAN_INSTALL"  # the image sets it to `container`
IMAGE: Final = "ghcr.io/mausa-ai/neosian"
UV_TOOL: Final = "uv-tool"
PIPX: Final = "pipx"
CONTAINER: Final = "container"
PROJECT: Final = "project"
UNKNOWN: Final = "unknown"


@dataclass(frozen=True, slots=True)
class Shape:
    """How this interpreter got neosian, and the command that moves it."""

    kind: str

    def upgrade_line(self, version: str) -> str:
        """The one command for this shape; `update` prints it, and applies
        it only for the uv tool shape (the three fences, §30)."""
        spec = shlex.quote(f"neosian=={version}")
        if self.kind == UV_TOOL:
            return f"uv tool install {spec}"
        if self.kind == PIPX:
            return f"pipx install --force {spec}"
        if self.kind == CONTAINER:
            return f"docker pull {IMAGE}:{version}"
        if self.kind == PROJECT:
            return f"uv add {spec}"
        return f"pip install --upgrade {spec}"


def detect_shape(prefix: Path, env: Mapping[str, str]) -> Shape:
    """`uv tool` leaves its receipt at the venv root, pipx its metadata, the
    image its environment marker; any other venv is a project's; the rest
    is unknown (a system interpreter, an editable checkout)."""
    if env.get(CONTAINER_ENV) == CONTAINER:
        return Shape(CONTAINER)
    if (prefix / "uv-receipt.toml").is_file():
        return Shape(UV_TOOL)
    if (prefix / "pipx_metadata.json").is_file():
        return Shape(PIPX)
    if (prefix / "pyvenv.cfg").is_file():
        return Shape(PROJECT)
    return Shape(UNKNOWN)
