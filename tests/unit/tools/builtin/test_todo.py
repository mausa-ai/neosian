"""Tests for the global todo list tool."""

import pytest

from neosian._foundation.tools.base import get_tool_definition, get_tool_metadata
from neosian._foundation.tools.builtin.todo import update_todo


@pytest.mark.unit
class TestUpdateTodoTool:
    """Tests for the stateless update_todo tool."""

    def test_is_decorated_function(self) -> None:
        """update_todo is a properly decorated tool function."""
        metadata = get_tool_metadata(update_todo)
        assert metadata is not None
        assert metadata.name == "update_todo"

    def test_has_tool_definition(self) -> None:
        """Todo tool has proper tool definition for LLM."""
        definition = get_tool_definition(update_todo)
        assert definition is not None
        assert definition.name == "update_todo"
        assert "task" in definition.description.lower()
        assert "replaces" in definition.description.lower()

    def test_schema_has_explicit_properties(self) -> None:
        """Todo tool schema has explicit content and status properties."""
        definition = get_tool_definition(update_todo)
        assert definition is not None

        # Check that todos is an array, described from tools.yaml (§27.9)
        todos_schema = definition.parameters["properties"]["todos"]
        assert todos_schema["type"] == "array"
        assert "'content'" in todos_schema["description"]

        # The item is a closed object under $defs (pydantic's shape)
        assert todos_schema["items"] == {"$ref": "#/$defs/TodoItemInput"}
        item_schema = definition.parameters["$defs"]["TodoItemInput"]
        assert item_schema["type"] == "object"
        assert "title" not in item_schema
        assert "description" not in item_schema  # no docstring reaches the model
        assert "properties" in item_schema
        assert "content" in item_schema["properties"]
        assert "status" in item_schema["properties"]

        # Check content is string
        assert item_schema["properties"]["content"]["type"] == "string"

        # Check status has enum values
        assert item_schema["properties"]["status"]["type"] == "string"
        assert item_schema["properties"]["status"]["enum"] == [
            "pending",
            "in_progress",
            "completed",
        ]

        # Check required fields
        assert set(item_schema["required"]) == {"content", "status"}

        # Check additionalProperties is false to prevent LLM hallucinating keys
        assert item_schema["additionalProperties"] is False

    @pytest.mark.asyncio
    async def test_returns_validated_items(self) -> None:
        """Tool validates and returns the input items."""
        result = await update_todo(
            todos=[
                {"content": "New task", "status": "pending"},
            ]
        )

        assert result.success
        assert result.data == [{"content": "New task", "status": "pending"}]

    @pytest.mark.asyncio
    async def test_returns_multiple_items(self) -> None:
        """Tool handles multiple items."""
        result = await update_todo(
            todos=[
                {"content": "Task A", "status": "pending"},
                {"content": "Task B", "status": "in_progress"},
                {"content": "Task C", "status": "completed"},
            ]
        )

        assert result.success
        assert result.data == [
            {"content": "Task A", "status": "pending"},
            {"content": "Task B", "status": "in_progress"},
            {"content": "Task C", "status": "completed"},
        ]

    @pytest.mark.asyncio
    async def test_invalid_status_fails(self) -> None:
        """An invalid status fails the call instead of being rewritten (TG-13)."""
        result = await update_todo(
            todos=[
                {"content": "Task A", "status": "pending"},
                {"content": "Task B", "status": "invalid_status"},
            ]
        )

        assert not result.success
        assert result.data is None
        assert result.error is not None
        assert "invalid_status" in result.error
        assert "todos[1]" in result.error
        for legal in ("pending", "in_progress", "completed"):
            assert legal in result.error

    @pytest.mark.asyncio
    async def test_missing_status_defaults_to_pending(self) -> None:
        """Missing status defaults to pending."""
        result = await update_todo(
            todos=[
                {"content": "Task without status"},
            ]
        )

        assert result.success
        assert result.data == [{"content": "Task without status", "status": "pending"}]

    @pytest.mark.asyncio
    async def test_missing_content_empty_string(self) -> None:
        """Missing content becomes empty string."""
        result = await update_todo(
            todos=[
                {"status": "pending"},
            ]
        )

        assert result.success
        assert result.data == [{"content": "", "status": "pending"}]

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self) -> None:
        """Passing empty list returns empty list."""
        result = await update_todo(todos=[])

        assert result.success
        assert result.data == []

    @pytest.mark.asyncio
    async def test_stateless_multiple_calls(self) -> None:
        """Each call is independent - no state carried between calls."""
        result1 = await update_todo(todos=[{"content": "First", "status": "pending"}])
        result2 = await update_todo(
            todos=[
                {"content": "Second", "status": "completed"},
                {"content": "Third", "status": "in_progress"},
            ]
        )

        # Each call returns only what was passed to it
        assert result1.data == [{"content": "First", "status": "pending"}]
        assert result2.data == [
            {"content": "Second", "status": "completed"},
            {"content": "Third", "status": "in_progress"},
        ]
