"""Shared YAML frontmatter — parse and render.

Markdown with a YAML block between `---` fence lines (skills, the
retired blackboard's entries). Fences are whole lines: a `---` inside a value never closes
the block. `render_frontmatter` is the writer's inverse — real YAML, so
a description carrying a `: ` survives a rewrite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

_FENCE = "---"


class FrontmatterError(Exception):
    """Raised when frontmatter parsing fails."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Invalid or missing frontmatter: {path}")
        self.path = path


def parse_frontmatter(content: str, path: str) -> tuple[dict[str, Any], str]:
    """Split markdown content into the YAML mapping and the stripped body.

    Raises:
        FrontmatterError: If frontmatter is missing, unclosed, or not a
            YAML mapping.
    """
    lines = content.strip().split("\n")
    if lines[0].rstrip("\r") != _FENCE:
        raise FrontmatterError(path)
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == _FENCE:
            break
    else:
        raise FrontmatterError(path)
    try:
        data = yaml.safe_load("\n".join(lines[1:index]))
    except yaml.YAMLError as exc:
        raise FrontmatterError(path) from exc
    if not isinstance(data, dict):
        raise FrontmatterError(path)
    return data, "\n".join(lines[index + 1 :]).strip()


def render_frontmatter(frontmatter: Mapping[str, Any], body: str) -> str:
    """The file text for a mapping and a body; `parse_frontmatter` reads
    it back. `width` keeps long values on one line so the fence scan
    stays trivially correct."""
    rendered = yaml.safe_dump(
        dict(frontmatter), sort_keys=False, allow_unicode=True, width=1_000_000
    )
    return f"{_FENCE}\n{rendered}{_FENCE}\n\n{body}\n"
