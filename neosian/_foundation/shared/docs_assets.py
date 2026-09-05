"""Shipped docs pages (ECOSYSTEM §8, DESIGN §14.4).

The pages `neosian docs` serves live in assets/docs/ as markdown +
frontmatter and travel in the wheel — version-true by construction.
Loaded and validated at module import, fail-fast: a malformed page is a
broken wheel, caught by the gate, never by a user.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Final

from neosian._foundation.shared.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
)

_PACKAGE: Final = "neosian.assets"
_DOCS_DIR: Final = "docs"
# The curated reading order — and the manifest: a page on disk that is
# not listed here, or a listed page missing from the wheel, fails at
# import instead of becoming invisible.
_TOPICS: Final = (
    "quickstart",
    "agent",
    "tools",
    "memory",
    "skills",
    "cli",
    "mcp",
    "agents",
    "topology",
)
_REQUIRED_KEYS: Final = ("title", "summary")
_SUMMARY_MAX: Final = 90  # the listing stays one line per topic


class DocsAssetError(Exception):
    """A shipped docs page is missing or malformed (a broken wheel)."""


@dataclass(frozen=True, slots=True)
class DocPage:
    """One shipped page; `topic` is the argv token."""

    topic: str
    title: str
    summary: str
    body: str


def _load() -> tuple[DocPage, ...]:
    docs = resources.files(_PACKAGE).joinpath(_DOCS_DIR)
    shipped = {entry.name for entry in docs.iterdir() if entry.name.endswith(".md")}
    expected = {f"{topic}.md" for topic in _TOPICS}
    if shipped != expected:
        raise DocsAssetError(
            f"assets/{_DOCS_DIR} does not match the topic manifest: "
            f"shipped {sorted(shipped)}, expected {sorted(expected)}"
        )
    pages: list[DocPage] = []
    for topic in _TOPICS:
        filename = f"{_DOCS_DIR}/{topic}.md"
        text = resources.files(_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
        try:
            meta, body = parse_frontmatter(text, filename)
        except FrontmatterError as exc:
            raise DocsAssetError(str(exc)) from exc
        for key in _REQUIRED_KEYS:
            value = meta.get(key)
            if not isinstance(value, str) or not value.strip():
                raise DocsAssetError(f"{filename}: missing or empty {key!r}")
        summary = meta["summary"]
        if "\n" in summary or len(summary) > _SUMMARY_MAX:
            raise DocsAssetError(
                f"{filename}: summary must be one line of at most "
                f"{_SUMMARY_MAX} characters"
            )
        if not body:
            raise DocsAssetError(f"{filename}: empty body")
        pages.append(
            DocPage(topic=topic, title=meta["title"], summary=summary, body=body)
        )
    return tuple(pages)


_PAGES: Final[tuple[DocPage, ...]] = _load()
_BY_TOPIC: Final[dict[str, DocPage]] = {page.topic: page for page in _PAGES}


def list_topics() -> tuple[DocPage, ...]:
    """Every shipped page, in curated reading order."""
    return _PAGES


def load_page(topic: str) -> DocPage | None:
    """One page by topic name; None when the topic is unknown."""
    return _BY_TOPIC.get(topic)
