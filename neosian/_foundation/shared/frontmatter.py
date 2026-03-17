"""Shared YAML frontmatter parser.

Parses markdown files with YAML frontmatter delimited by --- markers.
Used by both playbook loader and blackboard file provider.
"""

import yaml


class FrontmatterError(Exception):
    """Raised when frontmatter parsing fails."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Invalid or missing frontmatter: {path}")
        self.path = path


def parse_frontmatter(content: str, path: str) -> tuple[dict[str, str], str]:
    """Split markdown content into YAML frontmatter and body.

    Args:
        content: Raw file content.
        path: File path for error messages.

    Returns:
        Tuple of (frontmatter dict, body string).

    Raises:
        FrontmatterError: If frontmatter is missing or invalid YAML.
    """
    stripped = content.strip()

    if not stripped.startswith("---"):
        raise FrontmatterError(path)

    # Find closing ---
    end_index = stripped.find("---", 3)
    if end_index == -1:
        raise FrontmatterError(path)

    frontmatter_raw = stripped[3:end_index]
    body = stripped[end_index + 3 :]

    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as e:
        raise FrontmatterError(path) from e

    if not isinstance(data, dict):
        raise FrontmatterError(path)

    return data, body.strip()
