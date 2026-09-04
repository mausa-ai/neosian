"""The directory skill source: the address is the name (DESIGN §24.2)."""

from pathlib import Path

import pytest

from neosian._foundation.shared.exceptions import (
    SkillDirectoryNotFoundError,
    SkillFileNotFoundError,
    SkillInvalidFrontmatterError,
    SkillMissingKeyError,
)
from neosian._foundation.shared.skill import (
    load_skill,
    load_skills,
    skill_from_document,
)
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
class TestSkillFromDocument:
    def test_description_alone_suffices(self) -> None:
        skill = skill_from_document("release", "---\ndescription: d\n---\nbody", "x")
        assert skill.name == "release"
        assert skill.description == "d"
        assert skill.content == "body"

    def test_a_matching_name_key_is_accepted(self) -> None:
        skill = skill_from_document("code-review", VALID_SKILL, "x")
        assert skill.name == "code-review"
        assert "---" not in skill.content and "name:" not in skill.content

    def test_a_contradicting_name_key_is_refused_naming_both(self) -> None:
        with pytest.raises(SkillInvalidFrontmatterError) as exc_info:
            skill_from_document("other", VALID_SKILL, "/p/skills/other")
        assert "'code-review'" in str(exc_info.value)
        assert "'other'" in str(exc_info.value)
        assert "/p/skills/other" in str(exc_info.value)

    def test_missing_description(self) -> None:
        with pytest.raises(SkillMissingKeyError) as exc_info:
            skill_from_document("t", "---\nname: t\n---\nbody", "x")
        assert exc_info.value.key == "description"

    @pytest.mark.parametrize(
        "content",
        [
            "Just some markdown without frontmatter.",
            "---\ndescription: d\nno closing fence",
            "---\ndescription:\n  - item1\n---\nbody",
        ],
    )
    def test_invalid_frontmatter(self, content: str) -> None:
        with pytest.raises(SkillInvalidFrontmatterError):
            skill_from_document("t", content, "x")


@pytest.mark.unit
class TestLoadSkill:
    def test_the_stem_is_the_name(self, tmp_path: Path) -> None:
        (tmp_path / "code-review.md").write_text(VALID_SKILL)
        skill = load_skill(str(tmp_path / "code-review.md"))
        assert skill.name == "code-review"
        assert "SQL injection" in skill.content

    def test_a_name_key_must_match_the_stem(self, tmp_path: Path) -> None:
        (tmp_path / "review.md").write_text(VALID_SKILL)
        with pytest.raises(SkillInvalidFrontmatterError):
            load_skill(tmp_path / "review.md")

    def test_raises_on_file_not_found(self) -> None:
        with pytest.raises(SkillFileNotFoundError):
            load_skill("/nonexistent/path/skill.md")

    def test_skill_is_frozen(self, tmp_path: Path) -> None:
        (tmp_path / "code-review.md").write_text(VALID_SKILL)
        skill = load_skill(tmp_path / "code-review.md")
        with pytest.raises(AttributeError):
            skill.name = SkillName("modified")  # type: ignore[misc]


@pytest.mark.unit
class TestLoadSkills:
    def test_loads_directory_by_filename(self, tmp_path: Path) -> None:
        (tmp_path / "zulu.md").write_text("---\ndescription: Z\n---\nZ content.")
        (tmp_path / "alpha.md").write_text("---\ndescription: A\n---\nA content.")
        skills = load_skills(tmp_path)
        assert [s.name for s in skills] == ["alpha", "zulu"]

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert load_skills(tmp_path) == []

    def test_raises_on_directory_not_found(self) -> None:
        with pytest.raises(SkillDirectoryNotFoundError):
            load_skills("/nonexistent/directory/")

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "valid.md").write_text("---\ndescription: Valid\n---\nContent.")
        (tmp_path / "readme.txt").write_text("Not a skill.")
        (tmp_path / "config.yaml").write_text("key: value")
        assert [s.name for s in load_skills(tmp_path)] == ["valid"]
