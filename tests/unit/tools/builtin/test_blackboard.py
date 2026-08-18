"""Tests for the built-in blackboard tools."""

import pytest

from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.shared.types import BlackboardEntry, BlackboardName
from neosian._foundation.tools.base import get_tool_definition, get_tool_metadata
from neosian._foundation.tools.builtin.blackboard import create_blackboard_tools


class MockBlackboard(BlackboardProvider):
    """In-memory blackboard for testing."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str]] = {}  # name -> (description, content)

    def add_entry(self, name: str, description: str, content: str) -> None:
        self._entries[name] = (description, content)

    async def list_entries(self) -> list[BlackboardEntry]:
        return [
            BlackboardEntry(name=BlackboardName(name), description=desc)
            for name, (desc, _) in self._entries.items()
        ]

    async def read_entry(self, name: str) -> str | None:
        entry = self._entries.get(name)
        return entry[1] if entry else None

    async def update_entry(self, name: str, content: str) -> bool:
        if name not in self._entries:
            return False
        desc = self._entries[name][0]
        self._entries[name] = (desc, content)
        return True


def _make_blackboard() -> MockBlackboard:
    """Create a test blackboard with sample entries."""
    bb = MockBlackboard()
    bb.add_entry("workspace", "Media aliases", "image1: url1\nimage2: url2")
    bb.add_entry("notes", "Session notes", "User prefers dark mode")
    return bb


@pytest.mark.unit
class TestListBlackboardTool:
    """Tests for the list_blackboard tool."""

    def test_is_decorated_function(self) -> None:
        """list_blackboard is a properly decorated tool function."""
        list_bb, _, _ = create_blackboard_tools(_make_blackboard())
        metadata = get_tool_metadata(list_bb)
        assert metadata is not None
        assert metadata.name == "list_blackboard"

    def test_has_tool_definition(self) -> None:
        """list_blackboard has proper tool definition for LLM."""
        list_bb, _, _ = create_blackboard_tools(_make_blackboard())
        definition = get_tool_definition(list_bb)
        assert definition is not None
        assert definition.name == "list_blackboard"

    @pytest.mark.asyncio
    async def test_returns_all_entries(self) -> None:
        """list_blackboard returns names and descriptions."""
        list_bb, _, _ = create_blackboard_tools(_make_blackboard())
        result = await list_bb()

        assert result.success
        assert result.data is not None
        assert len(result.data) == 2
        names = {e["name"] for e in result.data}
        assert "workspace" in names
        assert "notes" in names

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """list_blackboard returns empty list when no entries."""
        list_bb, _, _ = create_blackboard_tools(MockBlackboard())
        result = await list_bb()

        assert result.success
        assert result.data == []


@pytest.mark.unit
class TestReadBlackboardTool:
    """Tests for the read_blackboard tool."""

    def test_is_decorated_function(self) -> None:
        """read_blackboard is a properly decorated tool function."""
        _, read_bb, _ = create_blackboard_tools(_make_blackboard())
        metadata = get_tool_metadata(read_bb)
        assert metadata is not None
        assert metadata.name == "read_blackboard"

    def test_has_name_parameter(self) -> None:
        """read_blackboard has a name parameter in schema."""
        _, read_bb, _ = create_blackboard_tools(_make_blackboard())
        definition = get_tool_definition(read_bb)
        assert definition is not None
        assert "name" in definition.parameters["properties"]

    @pytest.mark.asyncio
    async def test_reads_existing_entry(self) -> None:
        """read_blackboard returns content of existing entry."""
        _, read_bb, _ = create_blackboard_tools(_make_blackboard())
        result = await read_bb(name="workspace")

        assert result.success
        assert result.data is not None
        assert "image1: url1" in result.data

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self) -> None:
        """read_blackboard returns error with available names."""
        _, read_bb, _ = create_blackboard_tools(_make_blackboard())
        result = await read_bb(name="nonexistent")

        assert not result.success
        assert "nonexistent" in str(result.error)
        assert result.system_reminder is not None
        assert "workspace" in result.system_reminder


@pytest.mark.unit
class TestUpdateBlackboardTool:
    """Tests for the update_blackboard tool."""

    def test_is_decorated_function(self) -> None:
        """update_blackboard is a properly decorated tool function."""
        _, _, update_bb = create_blackboard_tools(_make_blackboard())
        metadata = get_tool_metadata(update_bb)
        assert metadata is not None
        assert metadata.name == "update_blackboard"

    def test_has_name_and_content_parameters(self) -> None:
        """update_blackboard has name and content parameters."""
        _, _, update_bb = create_blackboard_tools(_make_blackboard())
        definition = get_tool_definition(update_bb)
        assert definition is not None
        assert "name" in definition.parameters["properties"]
        assert "content" in definition.parameters["properties"]

    @pytest.mark.asyncio
    async def test_updates_existing_entry(self) -> None:
        """update_blackboard updates and confirms."""
        bb = _make_blackboard()
        _, read_bb, update_bb = create_blackboard_tools(bb)

        result = await update_bb(name="workspace", content="image3: url3")
        assert result.success
        assert result.data is not None
        assert "workspace" in result.data

        # Verify the update
        read_result = await read_bb(name="workspace")
        assert read_result.success
        assert read_result.data is not None
        assert "image3: url3" in read_result.data

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self) -> None:
        """update_blackboard returns error for nonexistent entry."""
        _, _, update_bb = create_blackboard_tools(_make_blackboard())
        result = await update_bb(name="nonexistent", content="data")

        assert not result.success
        assert "nonexistent" in str(result.error)
        assert result.system_reminder is not None
