"""Unit tests for FileBlackboard provider."""

from pathlib import Path

import pytest

from neosian._foundation.blackboard.file import FileBlackboard
from neosian._foundation.shared.exceptions import FileBlackboardDirectoryNotFoundError


def _write_entry(
    tmp_path: Path, filename: str, name: str, desc: str, body: str
) -> None:
    """Write a blackboard entry file."""
    (tmp_path / filename).write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"
    )


@pytest.mark.unit
class TestFileBlackboard:
    """Tests for the FileBlackboard provider."""

    def test_raises_on_missing_directory(self) -> None:
        """Constructor raises if directory doesn't exist."""
        with pytest.raises(FileBlackboardDirectoryNotFoundError):
            FileBlackboard("/nonexistent/directory/")

    @pytest.mark.asyncio
    async def test_list_entries(self, tmp_path: Path) -> None:
        """list_entries returns all .md files as entries."""
        _write_entry(
            tmp_path, "workspace.md", "workspace", "Media aliases", "img1: url1"
        )
        _write_entry(tmp_path, "notes.md", "notes", "Session notes", "Some notes")

        bb = FileBlackboard(tmp_path)
        entries = await bb.list_entries()

        assert len(entries) == 2
        names = {e.name for e in entries}
        assert "workspace" in names
        assert "notes" in names

    @pytest.mark.asyncio
    async def test_list_entries_empty(self, tmp_path: Path) -> None:
        """list_entries returns empty list for empty directory."""
        bb = FileBlackboard(tmp_path)
        entries = await bb.list_entries()
        assert entries == []

    @pytest.mark.asyncio
    async def test_read_entry(self, tmp_path: Path) -> None:
        """read_entry returns content from disk."""
        _write_entry(tmp_path, "workspace.md", "workspace", "Aliases", "image1: url1")

        bb = FileBlackboard(tmp_path)
        content = await bb.read_entry("workspace")

        assert content is not None
        assert "image1: url1" in content

    @pytest.mark.asyncio
    async def test_read_entry_not_found(self, tmp_path: Path) -> None:
        """read_entry returns None for nonexistent entry."""
        bb = FileBlackboard(tmp_path)
        content = await bb.read_entry("nonexistent")
        assert content is None

    @pytest.mark.asyncio
    async def test_read_entry_always_fresh(self, tmp_path: Path) -> None:
        """read_entry re-reads from disk each time (always fresh)."""
        _write_entry(tmp_path, "workspace.md", "workspace", "Aliases", "image1: url1")

        bb = FileBlackboard(tmp_path)

        # First read
        content1 = await bb.read_entry("workspace")
        assert content1 is not None
        assert "image1: url1" in content1

        # External update (app writes)
        _write_entry(
            tmp_path,
            "workspace.md",
            "workspace",
            "Aliases",
            "image1: url1\nimage2: url2",
        )

        # Second read sees the update
        content2 = await bb.read_entry("workspace")
        assert content2 is not None
        assert "image2: url2" in content2

    @pytest.mark.asyncio
    async def test_update_entry(self, tmp_path: Path) -> None:
        """update_entry writes new content preserving frontmatter."""
        _write_entry(tmp_path, "workspace.md", "workspace", "Aliases", "old content")

        bb = FileBlackboard(tmp_path)
        success = await bb.update_entry("workspace", "new content here")

        assert success is True

        # Verify by reading back
        content = await bb.read_entry("workspace")
        assert content is not None
        assert "new content here" in content
        assert "old content" not in content

    @pytest.mark.asyncio
    async def test_update_entry_not_found(self, tmp_path: Path) -> None:
        """update_entry returns False for nonexistent entry."""
        bb = FileBlackboard(tmp_path)
        success = await bb.update_entry("nonexistent", "content")
        assert success is False

    @pytest.mark.asyncio
    async def test_update_preserves_frontmatter(self, tmp_path: Path) -> None:
        """update_entry preserves name and description in frontmatter."""
        _write_entry(tmp_path, "workspace.md", "workspace", "Media aliases", "old")

        bb = FileBlackboard(tmp_path)
        await bb.update_entry("workspace", "new content")

        # Re-read to verify frontmatter preserved
        entries = await bb.list_entries()
        assert len(entries) == 1
        assert entries[0].name == "workspace"
        assert entries[0].description == "Media aliases"

    @pytest.mark.asyncio
    async def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        """Only .md files are considered."""
        _write_entry(tmp_path, "workspace.md", "workspace", "Aliases", "content")
        (tmp_path / "readme.txt").write_text("Not a blackboard entry.")

        bb = FileBlackboard(tmp_path)
        entries = await bb.list_entries()

        assert len(entries) == 1
        assert entries[0].name == "workspace"

    @pytest.mark.asyncio
    async def test_ignores_invalid_frontmatter(self, tmp_path: Path) -> None:
        """Files with invalid frontmatter are skipped."""
        _write_entry(tmp_path, "valid.md", "valid", "Valid entry", "content")
        (tmp_path / "invalid.md").write_text("No frontmatter here.")

        bb = FileBlackboard(tmp_path)
        entries = await bb.list_entries()

        assert len(entries) == 1
        assert entries[0].name == "valid"
