"""Skill loading — the directory source.

A skill is markdown with a frontmatter `description`; its name is its
address — the file stem here, the document path under `skills/` in a
mount (`memory/skills.py`, DESIGN §24). A `name` key is optional and must
match the address (the SKILL.md convention); `version` and `owner` never
appear in frontmatter — the version row and the scope are the truth.
"""

from pathlib import Path

from neosian._foundation.shared.constants import SkillLoader
from neosian._foundation.shared.exceptions import (
    SkillDirectoryNotFoundError,
    SkillFileNotFoundError,
    SkillInvalidFrontmatterError,
    SkillMissingKeyError,
)
from neosian._foundation.shared.frontmatter import FrontmatterError, parse_frontmatter
from neosian._foundation.shared.types import Skill, SkillName


def skill_from_document(name: str, content: str, where: str) -> Skill:
    """Parse a skill document whose address is `name`; `where` names the
    source in errors (a file path, a `/mount/skills/name` path).

    Raises:
        SkillInvalidFrontmatterError: No frontmatter, a non-string
            description, or a `name` key that contradicts the address.
        SkillMissingKeyError: No `description`.
    """
    try:
        frontmatter, body = parse_frontmatter(content, where)
    except FrontmatterError as err:
        raise SkillInvalidFrontmatterError(where) from err
    if SkillLoader.DESCRIPTION_KEY not in frontmatter:
        raise SkillMissingKeyError(SkillLoader.DESCRIPTION_KEY, where)
    description = frontmatter[SkillLoader.DESCRIPTION_KEY]
    if not isinstance(description, str):
        raise SkillInvalidFrontmatterError(where, "description must be a string")
    declared = frontmatter.get(SkillLoader.NAME_KEY, name)
    if declared != name:
        raise SkillInvalidFrontmatterError(
            where, f"name {declared!r} does not match the address {name!r}"
        )
    return Skill(name=SkillName(name), description=description, content=body)


def load_skill(path: str | Path) -> Skill:
    """Load one skill file; the file stem is its name.

    Raises:
        SkillFileNotFoundError: If the file does not exist.
        SkillInvalidFrontmatterError, SkillMissingKeyError: as
            `skill_from_document`.
    """
    path = Path(path)
    if not path.exists():
        raise SkillFileNotFoundError(str(path))
    return skill_from_document(path.stem, path.read_text(encoding="utf-8"), str(path))


def load_skills(directory: str | Path) -> list[Skill]:
    """Every `*.md` in `directory`, sorted by filename — stems are unique,
    so names are.

    Raises:
        SkillDirectoryNotFoundError: If the directory does not exist.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise SkillDirectoryNotFoundError(str(directory))
    return [load_skill(file) for file in sorted(directory.glob("*.md"))]
