"""Unit tests for playbook loading."""

import tempfile
from pathlib import Path

import pytest

from neosian._foundation.shared.exceptions import (
    PlaybookDirectoryNotFoundError,
    PlaybookDuplicateNameError,
    PlaybookFileNotFoundError,
    PlaybookInvalidFrontmatterError,
    PlaybookMissingKeyError,
)
from neosian._foundation.shared.playbook import load_playbook, load_playbooks
from neosian._foundation.shared.types import PlaybookName

VALID_PLAYBOOK = """\
---
name: code-review
description: Expert code review guidelines
---

When reviewing code, check for security issues first.

- Look for SQL injection
- Check for XSS
"""


@pytest.mark.unit
class TestLoadPlaybook:
    """Tests for load_playbook function."""

    def test_loads_valid_playbook(self) -> None:
        """Test loading a valid playbook with frontmatter."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_PLAYBOOK)
            f.flush()

            playbook = load_playbook(f.name)

            assert playbook.name == "code-review"
            assert playbook.description == "Expert code review guidelines"
            assert "security issues" in playbook.content
            assert "SQL injection" in playbook.content

    def test_content_excludes_frontmatter(self) -> None:
        """Content should not contain the frontmatter delimiters or YAML."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_PLAYBOOK)
            f.flush()

            playbook = load_playbook(f.name)

            assert "---" not in playbook.content
            assert "name:" not in playbook.content
            assert "description:" not in playbook.content

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(PlaybookFileNotFoundError):
            load_playbook("/nonexistent/path/playbook.md")

    def test_raises_on_no_frontmatter(self) -> None:
        """Test error when file has no frontmatter."""
        content = "Just some markdown without frontmatter."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(PlaybookInvalidFrontmatterError):
                load_playbook(f.name)

    def test_raises_on_unclosed_frontmatter(self) -> None:
        """Test error when frontmatter has no closing delimiter."""
        content = "---\nname: test\nSome content without closing ---"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(PlaybookInvalidFrontmatterError):
                load_playbook(f.name)

    def test_raises_on_missing_name(self) -> None:
        """Test error when name key is missing."""
        content = "---\ndescription: Some description\n---\nContent here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(PlaybookMissingKeyError) as exc_info:
                load_playbook(f.name)
            assert exc_info.value.key == "name"

    def test_raises_on_missing_description(self) -> None:
        """Test error when description key is missing."""
        content = "---\nname: test-playbook\n---\nContent here."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(PlaybookMissingKeyError) as exc_info:
                load_playbook(f.name)
            assert exc_info.value.key == "description"

    def test_raises_on_non_string_name(self) -> None:
        """Test error when name is not a string."""
        content = "---\nname:\n  - item1\ndescription: Desc\n---\nContent."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()

            with pytest.raises(PlaybookInvalidFrontmatterError):
                load_playbook(f.name)

    def test_accepts_path_object(self) -> None:
        """Test that Path objects are accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_PLAYBOOK)
            f.flush()

            playbook = load_playbook(Path(f.name))

            assert playbook.name == "code-review"

    def test_playbook_is_frozen(self) -> None:
        """Playbook dataclass is immutable."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(VALID_PLAYBOOK)
            f.flush()

            playbook = load_playbook(f.name)

            with pytest.raises(AttributeError):
                playbook.name = PlaybookName("modified")  # type: ignore[misc]


@pytest.mark.unit
class TestLoadPlaybooks:
    """Tests for load_playbooks function."""

    def test_loads_directory(self, tmp_path: Path) -> None:
        """Test loading multiple playbooks from a directory."""
        (tmp_path / "alpha.md").write_text(
            "---\nname: alpha\ndescription: Alpha playbook\n---\nAlpha content."
        )
        (tmp_path / "beta.md").write_text(
            "---\nname: beta\ndescription: Beta playbook\n---\nBeta content."
        )

        playbooks = load_playbooks(tmp_path)

        assert len(playbooks) == 2
        assert playbooks[0].name == "alpha"
        assert playbooks[1].name == "beta"

    def test_alphabetical_ordering(self, tmp_path: Path) -> None:
        """Files are sorted alphabetically for deterministic ordering."""
        (tmp_path / "z-playbook.md").write_text(
            "---\nname: zulu\ndescription: Z\n---\nZ content."
        )
        (tmp_path / "a-playbook.md").write_text(
            "---\nname: alpha\ndescription: A\n---\nA content."
        )

        playbooks = load_playbooks(tmp_path)

        assert playbooks[0].name == "alpha"
        assert playbooks[1].name == "zulu"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty list."""
        playbooks = load_playbooks(tmp_path)
        assert playbooks == []

    def test_raises_on_directory_not_found(self) -> None:
        """Test error when directory doesn't exist."""
        with pytest.raises(PlaybookDirectoryNotFoundError):
            load_playbooks("/nonexistent/directory/")

    def test_raises_on_duplicate_names(self, tmp_path: Path) -> None:
        """Test error when two playbooks have the same name."""
        (tmp_path / "first.md").write_text(
            "---\nname: duplicate\ndescription: First\n---\nFirst content."
        )
        (tmp_path / "second.md").write_text(
            "---\nname: duplicate\ndescription: Second\n---\nSecond content."
        )

        with pytest.raises(PlaybookDuplicateNameError) as exc_info:
            load_playbooks(tmp_path)
        assert exc_info.value.name == "duplicate"

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        """Only .md files are loaded."""
        (tmp_path / "playbook.md").write_text(
            "---\nname: valid\ndescription: Valid\n---\nContent."
        )
        (tmp_path / "readme.txt").write_text("Not a playbook.")
        (tmp_path / "config.yaml").write_text("key: value")

        playbooks = load_playbooks(tmp_path)

        assert len(playbooks) == 1
        assert playbooks[0].name == "valid"
