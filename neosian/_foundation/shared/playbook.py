"""Playbook loading utility.

Loads playbooks from markdown files with YAML frontmatter.
Mirrors the prompt.py loader pattern.
"""

from pathlib import Path

import yaml

from neosian._foundation.shared.constants import PlaybookLoader
from neosian._foundation.shared.exceptions import (
    PlaybookDirectoryNotFoundError,
    PlaybookDuplicateNameError,
    PlaybookFileNotFoundError,
    PlaybookInvalidFrontmatterError,
    PlaybookMissingKeyError,
)
from neosian._foundation.shared.types import Playbook, PlaybookName


def _parse_frontmatter(content: str, path_str: str) -> tuple[dict[str, str], str]:
    """Split markdown content into YAML frontmatter and body.

    Args:
        content: Raw file content.
        path_str: File path for error messages.

    Returns:
        Tuple of (frontmatter dict, body string).

    Raises:
        PlaybookInvalidFrontmatterError: If frontmatter is missing or invalid.
    """
    stripped = content.strip()

    if not stripped.startswith("---"):
        raise PlaybookInvalidFrontmatterError(path_str)

    # Find closing ---
    end_index = stripped.find("---", 3)
    if end_index == -1:
        raise PlaybookInvalidFrontmatterError(path_str)

    frontmatter_raw = stripped[3:end_index]
    body = stripped[end_index + 3 :]

    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        raise PlaybookInvalidFrontmatterError(path_str) from e

    if not isinstance(data, dict):
        raise PlaybookInvalidFrontmatterError(path_str)

    return data, body.strip()


def load_playbook(path: str | Path) -> Playbook:
    """Load a single playbook from a markdown file with YAML frontmatter.

    The file must have YAML frontmatter between --- delimiters with
    'name' and 'description' keys.

    Example file:
        ---
        name: code-review
        description: Expert code review guidelines
        ---

        When reviewing code, check for security issues first...

    Args:
        path: Path to the markdown playbook file.

    Returns:
        A Playbook with name, description, and content.

    Raises:
        PlaybookFileNotFoundError: If the file does not exist.
        PlaybookInvalidFrontmatterError: If frontmatter is missing or invalid.
        PlaybookMissingKeyError: If 'name' or 'description' is missing.
    """
    path = Path(path)
    path_str = str(path)

    if not path.exists():
        raise PlaybookFileNotFoundError(path_str)

    content = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(content, path_str)

    if PlaybookLoader.NAME_KEY not in frontmatter:
        raise PlaybookMissingKeyError(PlaybookLoader.NAME_KEY, path_str)

    if PlaybookLoader.DESCRIPTION_KEY not in frontmatter:
        raise PlaybookMissingKeyError(PlaybookLoader.DESCRIPTION_KEY, path_str)

    name = frontmatter[PlaybookLoader.NAME_KEY]
    description = frontmatter[PlaybookLoader.DESCRIPTION_KEY]

    if not isinstance(name, str) or not isinstance(description, str):
        raise PlaybookInvalidFrontmatterError(path_str)

    return Playbook(
        name=PlaybookName(name),
        description=description,
        content=body,
    )


def load_playbooks(directory: str | Path) -> list[Playbook]:
    """Load all .md playbooks from a directory.

    Files are sorted alphabetically for deterministic ordering.
    Duplicate playbook names across files raise an error.

    Args:
        directory: Path to the directory containing playbook .md files.

    Returns:
        List of loaded Playbooks, sorted by filename.

    Raises:
        PlaybookDirectoryNotFoundError: If the directory does not exist.
        PlaybookDuplicateNameError: If two playbooks share the same name.
        PlaybookFileNotFoundError: If a file cannot be read.
        PlaybookInvalidFrontmatterError: If a file has invalid frontmatter.
        PlaybookMissingKeyError: If a file is missing required keys.
    """
    directory = Path(directory)

    if not directory.is_dir():
        raise PlaybookDirectoryNotFoundError(str(directory))

    md_files = sorted(directory.glob("*.md"))

    playbooks: list[Playbook] = []
    seen_names: set[str] = set()

    for md_file in md_files:
        playbook = load_playbook(md_file)
        if playbook.name in seen_names:
            raise PlaybookDuplicateNameError(playbook.name)
        seen_names.add(playbook.name)
        playbooks.append(playbook)

    return playbooks
