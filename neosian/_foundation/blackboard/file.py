"""File-based blackboard provider.

Each entry is a .md file with YAML frontmatter (name, description)
and markdown body as content. Reads from disk on every call (always fresh).
"""

from pathlib import Path

from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.shared.constants import SkillLoader
from neosian._foundation.shared.exceptions import FileBlackboardDirectoryNotFoundError
from neosian._foundation.shared.frontmatter import FrontmatterError, parse_frontmatter
from neosian._foundation.shared.types import BlackboardEntry, BlackboardName


class FileBlackboard(BlackboardProvider):
    """File-based blackboard provider.

    Each entry is a .md file with YAML frontmatter (name, description)
    and markdown body as content. Reads from disk on every call,
    so the agent always sees the latest content.

    File format (same as skills):
        ---
        name: workspace
        description: Generated media aliases
        ---

        image1: https://cdn.example.com/img1.png
        image2: https://cdn.example.com/img2.png

    Args:
        directory: Path to directory containing .md blackboard files.

    Raises:
        FileBlackboardDirectoryNotFoundError: If directory does not exist.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        if not self._directory.is_dir():
            raise FileBlackboardDirectoryNotFoundError(str(self._directory))

    def _scan_files(self) -> list[tuple[Path, dict[str, str], str]]:
        """Scan directory for .md files and parse their frontmatter.

        Returns:
            List of (file_path, frontmatter_dict, body) tuples.
        """
        results: list[tuple[Path, dict[str, str], str]] = []
        for md_file in sorted(self._directory.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                frontmatter, body = parse_frontmatter(content, str(md_file))
                name = frontmatter.get(SkillLoader.NAME_KEY)
                desc = frontmatter.get(SkillLoader.DESCRIPTION_KEY)
                if isinstance(name, str) and isinstance(desc, str):
                    results.append((md_file, frontmatter, body))
            except (FrontmatterError, OSError):
                # Skip files with invalid frontmatter or read errors
                continue
        return results

    def _find_file(self, name: str) -> tuple[Path, dict[str, str], str] | None:
        """Find a specific entry by name.

        Args:
            name: The entry name to find.

        Returns:
            Tuple of (path, frontmatter, body) or None if not found.
        """
        for path, frontmatter, body in self._scan_files():
            if frontmatter.get(SkillLoader.NAME_KEY) == name:
                return path, frontmatter, body
        return None

    async def list_entries(self) -> list[BlackboardEntry]:
        """List all .md files in directory as blackboard entries."""
        entries: list[BlackboardEntry] = []
        for _, frontmatter, _ in self._scan_files():
            name = frontmatter[SkillLoader.NAME_KEY]
            description = frontmatter[SkillLoader.DESCRIPTION_KEY]
            entries.append(
                BlackboardEntry(
                    name=BlackboardName(name),
                    description=description,
                )
            )
        return entries

    async def read_entry(self, name: str) -> str | None:
        """Read entry content fresh from disk."""
        result = self._find_file(name)
        if result is None:
            return None
        _, _, body = result
        return body

    async def update_entry(self, name: str, content: str) -> bool:
        """Update entry body while preserving frontmatter."""
        result = self._find_file(name)
        if result is None:
            return False
        path, frontmatter, _ = result

        # Reconstruct file with original frontmatter and new body
        fm_lines: list[str] = []
        for key, value in frontmatter.items():
            fm_lines.append(f"{key}: {value}")
        fm_text = "\n".join(fm_lines)

        new_content = f"---\n{fm_text}\n---\n\n{content}\n"
        path.write_text(new_content, encoding="utf-8")
        return True
