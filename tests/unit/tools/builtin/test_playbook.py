"""Tests for the built-in playbook tools."""

import pytest

from neosian._foundation.shared.types import Playbook, PlaybookName
from neosian._foundation.tools.base import get_tool_definition, get_tool_metadata
from neosian._foundation.tools.builtin.playbook import create_playbook_tools


def _make_playbooks() -> list[Playbook]:
    """Create test playbooks."""
    return [
        Playbook(
            name=PlaybookName("code-review"),
            description="Expert code review guidelines",
            content="Check for security issues first.",
        ),
        Playbook(
            name=PlaybookName("testing"),
            description="Testing best practices",
            content="Write unit tests for all public functions.",
        ),
    ]


@pytest.mark.unit
class TestListPlaybooksTool:
    """Tests for the list_playbooks tool."""

    def test_is_decorated_function(self) -> None:
        """list_playbooks is a properly decorated tool function."""
        list_pb, _ = create_playbook_tools(_make_playbooks())
        metadata = get_tool_metadata(list_pb)
        assert metadata is not None
        assert metadata.name == "list_playbooks"

    def test_has_tool_definition(self) -> None:
        """list_playbooks has proper tool definition for LLM."""
        list_pb, _ = create_playbook_tools(_make_playbooks())
        definition = get_tool_definition(list_pb)
        assert definition is not None
        assert definition.name == "list_playbooks"
        assert "playbook" in definition.description.lower()

    @pytest.mark.asyncio
    async def test_returns_all_playbooks(self) -> None:
        """list_playbooks returns names and descriptions of all playbooks."""
        list_pb, _ = create_playbook_tools(_make_playbooks())
        result = await list_pb()

        assert result.success
        assert result.data is not None
        assert len(result.data) == 2
        assert result.data[0] == {
            "name": "code-review",
            "description": "Expert code review guidelines",
        }
        assert result.data[1] == {
            "name": "testing",
            "description": "Testing best practices",
        }

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """list_playbooks returns empty list when no playbooks configured."""
        list_pb, _ = create_playbook_tools([])
        result = await list_pb()

        assert result.success
        assert result.data == []


@pytest.mark.unit
class TestLoadPlaybookTool:
    """Tests for the load_playbook tool."""

    def test_is_decorated_function(self) -> None:
        """load_playbook is a properly decorated tool function."""
        _, load_pb = create_playbook_tools(_make_playbooks())
        metadata = get_tool_metadata(load_pb)
        assert metadata is not None
        assert metadata.name == "load_playbook"

    def test_has_tool_definition(self) -> None:
        """load_playbook has proper tool definition for LLM."""
        _, load_pb = create_playbook_tools(_make_playbooks())
        definition = get_tool_definition(load_pb)
        assert definition is not None
        assert definition.name == "load_playbook"
        assert "name" in definition.parameters["properties"]

    @pytest.mark.asyncio
    async def test_loads_existing_playbook(self) -> None:
        """load_playbook returns content of an existing playbook."""
        _, load_pb = create_playbook_tools(_make_playbooks())
        result = await load_pb(name="code-review")

        assert result.success
        assert result.data == "Check for security issues first."

    @pytest.mark.asyncio
    async def test_loads_second_playbook(self) -> None:
        """load_playbook can load different playbooks by name."""
        _, load_pb = create_playbook_tools(_make_playbooks())
        result = await load_pb(name="testing")

        assert result.success
        assert result.data == "Write unit tests for all public functions."

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self) -> None:
        """load_playbook returns error with available names for unknown playbook."""
        _, load_pb = create_playbook_tools(_make_playbooks())
        result = await load_pb(name="nonexistent")

        assert not result.success
        assert "nonexistent" in str(result.error)
        assert result.system_reminder is not None
        assert "code-review" in result.system_reminder
        assert "testing" in result.system_reminder
