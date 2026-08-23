"""Skill loading utility.

Loads skills from markdown files with YAML frontmatter.
Mirrors the prompt.py loader pattern.
"""

from pathlib import Path

from neosian._foundation.shared.constants import SkillLoader
from neosian._foundation.shared.exceptions import (
    SkillDirectoryNotFoundError,
    SkillDuplicateNameError,
    SkillFileNotFoundError,
    SkillInvalidFrontmatterError,
    SkillMissingKeyError,
)
from neosian._foundation.shared.frontmatter import FrontmatterError, parse_frontmatter
from neosian._foundation.shared.types import Skill, SkillName


def load_skill(path: str | Path) -> Skill:
    """Load a single skill from a markdown file with YAML frontmatter.

    The file must have YAML frontmatter between --- delimiters with
    'name' and 'description' keys.

    Example file:
        ---
        name: code-review
        description: Expert code review guidelines
        ---

        When reviewing code, check for security issues first...

    Args:
        path: Path to the markdown skill file.

    Returns:
        A Skill with name, description, and content.

    Raises:
        SkillFileNotFoundError: If the file does not exist.
        SkillInvalidFrontmatterError: If frontmatter is missing or invalid.
        SkillMissingKeyError: If 'name' or 'description' is missing.
    """
    path = Path(path)
    path_str = str(path)

    if not path.exists():
        raise SkillFileNotFoundError(path_str)

    content = path.read_text(encoding="utf-8")

    try:
        frontmatter, body = parse_frontmatter(content, path_str)
    except FrontmatterError as err:
        raise SkillInvalidFrontmatterError(path_str) from err

    if SkillLoader.NAME_KEY not in frontmatter:
        raise SkillMissingKeyError(SkillLoader.NAME_KEY, path_str)

    if SkillLoader.DESCRIPTION_KEY not in frontmatter:
        raise SkillMissingKeyError(SkillLoader.DESCRIPTION_KEY, path_str)

    name = frontmatter[SkillLoader.NAME_KEY]
    description = frontmatter[SkillLoader.DESCRIPTION_KEY]

    if not isinstance(name, str) or not isinstance(description, str):
        raise SkillInvalidFrontmatterError(path_str)

    return Skill(
        name=SkillName(name),
        description=description,
        content=body,
    )


def load_skills(directory: str | Path) -> list[Skill]:
    """Load all .md skills from a directory.

    Files are sorted alphabetically for deterministic ordering.
    Duplicate skill names across files raise an error.

    Args:
        directory: Path to the directory containing skill .md files.

    Returns:
        List of loaded Skills, sorted by filename.

    Raises:
        SkillDirectoryNotFoundError: If the directory does not exist.
        SkillDuplicateNameError: If two skills share the same name.
        SkillFileNotFoundError: If a file cannot be read.
        SkillInvalidFrontmatterError: If a file has invalid frontmatter.
        SkillMissingKeyError: If a file is missing required keys.
    """
    directory = Path(directory)

    if not directory.is_dir():
        raise SkillDirectoryNotFoundError(str(directory))

    md_files = sorted(directory.glob("*.md"))

    skills: list[Skill] = []
    seen_names: set[str] = set()

    for md_file in md_files:
        skill = load_skill(md_file)
        if skill.name in seen_names:
            raise SkillDuplicateNameError(skill.name)
        seen_names.add(skill.name)
        skills.append(skill)

    return skills
