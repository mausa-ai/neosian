"""Unit tests for skill loading."""

import tempfile
from pathlib import Path

import pytest

from neosian._foundation.shared.exceptions import (
    SkillDirectoryNotFoundError,
    SkillDuplicateNameError,
    SkillFileNotFoundError,
    SkillInvalidFrontmatterError,
    SkillMissingKeyError,
)
from neosian._foundation.shared.skill import load_skill, load_skills
from neosian._foundation.shared.types import SkillName

VALID_SKILL = """\
---
name: code-review
description: Expert code review guidelines
---

When reviewing code, check for security issues first.

- Look for SQL injection
- Check for XSS
"""


@pytest.mark.unit
class TestLoadSkill:
    """Tests for load_skill function."""

    def test_loads_valid_skill(self) -> None:
        """Test loading a valid skill with frontmatter."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_SKILL)
            f.flush()

            skill = load_skill(f.name)

            assert skill.name == "code-review"
            assert skill.description == "Expert code review guidelines"
            assert "security issues" in skill.content
            assert "SQL injection" in skill.content

    def test_content_excludes_frontmatter(self) -> None:
        """Content should not contain the frontmatter delimiters or YAML."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_SKILL)
            f.flush()

            skill = load_skill(f.name)

            assert "---" not in skill.content
            assert "name:" not in skill.content
            assert "description:" not in skill.content

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(SkillFileNotFoundError):
            load_skill("/nonexistent/path/skill.md")

    def test_raises_on_no_frontmatter(self) -> None:
        """Test error when file has no frontmatter."""
        content = "Just some markdown without frontmatter."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(SkillInvalidFrontmatterError):
                load_skill(f.name)

    def test_raises_on_unclosed_frontmatter(self) -> None:
        """Test error when frontmatter has no closing delimiter."""
        content = "---\nname: test\nSome content without closing ---"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(SkillInvalidFrontmatterError):
                load_skill(f.name)

    def test_raises_on_missing_name(self) -> None:
        """Test error when name key is missing."""
        content = "---\ndescription: Some description\n---\nContent here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(SkillMissingKeyError) as exc_info:
                load_skill(f.name)
            assert exc_info.value.key == "name"

    def test_raises_on_missing_description(self) -> None:
        """Test error when description key is missing."""
        content = "---\nname: test-skill\n---\nContent here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(SkillMissingKeyError) as exc_info:
                load_skill(f.name)
            assert exc_info.value.key == "description"

    def test_raises_on_non_string_name(self) -> None:
        """Test error when name is not a string."""
        content = "---\nname:\n  - item1\ndescription: Desc\n---\nContent."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(SkillInvalidFrontmatterError):
                load_skill(f.name)

    def test_accepts_path_object(self) -> None:
        """Test that Path objects are accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_SKILL)
            f.flush()

            skill = load_skill(Path(f.name))

            assert skill.name == "code-review"

    def test_skill_is_frozen(self) -> None:
        """Skill dataclass is immutable."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_SKILL)
            f.flush()

            skill = load_skill(f.name)

            with pytest.raises(AttributeError):
                skill.name = SkillName("modified")  # type: ignore[misc]


@pytest.mark.unit
class TestLoadSkills:
    """Tests for load_skills function."""

    def test_loads_directory(self, tmp_path: Path) -> None:
        """Test loading multiple skills from a directory."""
        (tmp_path / "alpha.md").write_text(
            "---\nname: alpha\ndescription: Alpha skill\n---\nAlpha content."
        )
        (tmp_path / "beta.md").write_text(
            "---\nname: beta\ndescription: Beta skill\n---\nBeta content."
        )

        skills = load_skills(tmp_path)

        assert len(skills) == 2
        assert skills[0].name == "alpha"
        assert skills[1].name == "beta"

    def test_alphabetical_ordering(self, tmp_path: Path) -> None:
        """Files are sorted alphabetically for deterministic ordering."""
        (tmp_path / "z-skill.md").write_text(
            "---\nname: zulu\ndescription: Z\n---\nZ content."
        )
        (tmp_path / "a-skill.md").write_text(
            "---\nname: alpha\ndescription: A\n---\nA content."
        )

        skills = load_skills(tmp_path)

        assert skills[0].name == "alpha"
        assert skills[1].name == "zulu"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty list."""
        skills = load_skills(tmp_path)
        assert skills == []

    def test_raises_on_directory_not_found(self) -> None:
        """Test error when directory doesn't exist."""
        with pytest.raises(SkillDirectoryNotFoundError):
            load_skills("/nonexistent/directory/")

    def test_raises_on_duplicate_names(self, tmp_path: Path) -> None:
        """Test error when two skills have the same name."""
        (tmp_path / "first.md").write_text(
            "---\nname: duplicate\ndescription: First\n---\nFirst content."
        )
        (tmp_path / "second.md").write_text(
            "---\nname: duplicate\ndescription: Second\n---\nSecond content."
        )

        with pytest.raises(SkillDuplicateNameError) as exc_info:
            load_skills(tmp_path)
        assert exc_info.value.name == "duplicate"

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        """Only .md files are loaded."""
        (tmp_path / "skill.md").write_text(
            "---\nname: valid\ndescription: Valid\n---\nContent."
        )
        (tmp_path / "readme.txt").write_text("Not a skill.")
        (tmp_path / "config.yaml").write_text("key: value")

        skills = load_skills(tmp_path)

        assert len(skills) == 1
        assert skills[0].name == "valid"
