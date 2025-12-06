"""Tests for type definitions."""

import pytest

from neosian._foundation.shared.types import (
    AgentName,
    ModelId,
    ToolName,
)


@pytest.mark.unit
class TestTypeDefinitions:
    """Test that NewType definitions work correctly."""

    def test_newtypes_are_str_compatible(self) -> None:
        """NewTypes should be string-compatible for runtime use."""
        agent = AgentName("test-agent")
        tool = ToolName("my_tool")
        model = ModelId("llama-3.3-70b-versatile")

        # Should work as strings
        assert agent.startswith("test")
        assert "_" in tool
        assert len(model) > 0
