"""Skill loading errors (DESIGN §24)."""

from __future__ import annotations

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError


# Skill Errors
class SkillLoadError(NeosianError):
    """Base exception for skill loading errors."""

    code = "skill_load_failed"


class SkillFileNotFoundError(SkillLoadError):
    """Raised when a skill file is not found."""

    code = "skill_file_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.SKILL_FILE_NOT_FOUND.format(path=path), details={"path": path}
        )
        self.path = path


class SkillInvalidFrontmatterError(SkillLoadError):
    """Raised when a skill file has invalid or missing frontmatter."""

    code = "skill_invalid_frontmatter"

    def __init__(self, path: str, reason: str | None = None) -> None:
        message = ErrorMessages.SKILL_INVALID_FRONTMATTER.format(path=path)
        super().__init__(
            message if reason is None else f"{message} — {reason}",
            details={"path": path, "reason": reason},
        )
        self.path = path


class SkillMissingKeyError(SkillLoadError):
    """Raised when a required key is missing from skill frontmatter."""

    code = "skill_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(
            ErrorMessages.SKILL_MISSING_KEY.format(key=key, path=path),
            details={"key": key, "path": path},
        )
        self.key = key
        self.path = path


class SkillDuplicateNameError(SkillLoadError):
    """Raised when multiple skills share the same name."""

    code = "skill_duplicate_name"

    def __init__(self, name: str) -> None:
        super().__init__(
            ErrorMessages.SKILL_DUPLICATE_NAME.format(name=name), details={"name": name}
        )
        self.name = name


class SkillDirectoryNotFoundError(SkillLoadError):
    """Raised when the skill directory does not exist."""

    code = "skill_directory_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.SKILL_DIRECTORY_NOT_FOUND.format(path=path),
            details={"path": path},
        )
        self.path = path
