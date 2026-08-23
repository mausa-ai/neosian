"""Tests for the built-in skill tools."""

import pytest

from neosian._foundation.shared.types import Skill, SkillName
from neosian._foundation.tools.base import get_tool_definition, get_tool_metadata
from neosian._foundation.tools.builtin.skill import create_skill_tools


def _make_skills() -> list[Skill]:
    """Create test skills."""
    return [
        Skill(
            name=SkillName("code-review"),
            description="Expert code review guidelines",
            content="Check for security issues first.",
        ),
        Skill(
            name=SkillName("testing"),
            description="Testing best practices",
            content="Write unit tests for all public functions.",
        ),
    ]


@pytest.mark.unit
class TestListSkillsTool:
    """Tests for the list_skills tool."""

    def test_is_decorated_function(self) -> None:
        """list_skills is a properly decorated tool function."""
        list_pb, _ = create_skill_tools(_make_skills())
        metadata = get_tool_metadata(list_pb)
        assert metadata is not None
        assert metadata.name == "list_skills"

    def test_has_tool_definition(self) -> None:
        """list_skills has proper tool definition for LLM."""
        list_pb, _ = create_skill_tools(_make_skills())
        definition = get_tool_definition(list_pb)
        assert definition is not None
        assert definition.name == "list_skills"
        assert "skill" in definition.description.lower()

    @pytest.mark.asyncio
    async def test_returns_all_skills(self) -> None:
        """list_skills returns names and descriptions of all skills."""
        list_pb, _ = create_skill_tools(_make_skills())
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
        """list_skills returns empty list when no skills configured."""
        list_pb, _ = create_skill_tools([])
        result = await list_pb()

        assert result.success
        assert result.data == []


@pytest.mark.unit
class TestLoadSkillTool:
    """Tests for the load_skill tool."""

    def test_is_decorated_function(self) -> None:
        """load_skill is a properly decorated tool function."""
        _, load_pb = create_skill_tools(_make_skills())
        metadata = get_tool_metadata(load_pb)
        assert metadata is not None
        assert metadata.name == "load_skill"

    def test_has_tool_definition(self) -> None:
        """load_skill has proper tool definition for LLM."""
        _, load_pb = create_skill_tools(_make_skills())
        definition = get_tool_definition(load_pb)
        assert definition is not None
        assert definition.name == "load_skill"
        assert "name" in definition.parameters["properties"]

    @pytest.mark.asyncio
    async def test_loads_existing_skill(self) -> None:
        """load_skill returns content of an existing skill."""
        _, load_pb = create_skill_tools(_make_skills())
        result = await load_pb(name="code-review")

        assert result.success
        assert result.data == "Check for security issues first."

    @pytest.mark.asyncio
    async def test_loads_second_skill(self) -> None:
        """load_skill can load different skills by name."""
        _, load_pb = create_skill_tools(_make_skills())
        result = await load_pb(name="testing")

        assert result.success
        assert result.data == "Write unit tests for all public functions."

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self) -> None:
        """load_skill returns error with available names for unknown skill."""
        _, load_pb = create_skill_tools(_make_skills())
        result = await load_pb(name="nonexistent")

        assert not result.success
        assert "nonexistent" in str(result.error)
        assert result.system_reminder is not None
        assert "code-review" in result.system_reminder
        assert "testing" in result.system_reminder
