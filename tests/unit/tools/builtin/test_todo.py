"""Tests for the global todo list tool."""

import pytest

from neosian._foundation.shared.types import TodoStatus
from neosian._foundation.tools.base import get_tool_definition, get_tool_metadata
from neosian._foundation.tools.builtin.todo import (
    TodoItem,
    TodoState,
    get_todo_tool,
)


@pytest.mark.unit
class TestTodoItem:
    """Tests for TodoItem dataclass."""

    def test_create_todo_item(self) -> None:
        """TodoItem can be created with content and status."""
        item = TodoItem(content="Write tests", status=TodoStatus.PENDING)
        assert item.content == "Write tests"
        assert item.status == TodoStatus.PENDING

    def test_to_dict(self) -> None:
        """TodoItem converts to dictionary correctly."""
        item = TodoItem(content="Review code", status=TodoStatus.IN_PROGRESS)
        result = item.to_dict()
        assert result == {"content": "Review code", "status": "in_progress"}

    def test_to_dict_completed(self) -> None:
        """TodoItem with completed status converts correctly."""
        item = TodoItem(content="Deploy", status=TodoStatus.COMPLETED)
        result = item.to_dict()
        assert result == {"content": "Deploy", "status": "completed"}


@pytest.mark.unit
class TestTodoState:
    """Tests for TodoState dataclass."""

    def test_empty_state(self) -> None:
        """TodoState can be created empty."""
        state = TodoState(items=[])
        assert state.items == []
        assert state.to_list() == []

    def test_state_with_items(self) -> None:
        """TodoState holds multiple items."""
        state = TodoState(
            items=[
                TodoItem(content="Task 1", status=TodoStatus.PENDING),
                TodoItem(content="Task 2", status=TodoStatus.COMPLETED),
            ]
        )
        assert len(state.items) == 2

    def test_to_list(self) -> None:
        """TodoState converts all items to list of dicts."""
        state = TodoState(
            items=[
                TodoItem(content="First", status=TodoStatus.PENDING),
                TodoItem(content="Second", status=TodoStatus.IN_PROGRESS),
            ]
        )
        result = state.to_list()
        assert result == [
            {"content": "First", "status": "pending"},
            {"content": "Second", "status": "in_progress"},
        ]


@pytest.mark.unit
class TestGetTodoTool:
    """Tests for the todo tool factory."""

    def test_creates_decorated_function(self) -> None:
        """get_todo_tool returns a properly decorated tool function."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        metadata = get_tool_metadata(tool)
        assert metadata is not None
        assert metadata.name == "update_todo"

    def test_has_tool_definition(self) -> None:
        """Todo tool has proper tool definition for LLM."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        definition = get_tool_definition(tool)
        assert definition is not None
        assert definition.name == "update_todo"
        assert "task" in definition.description.lower()
        assert "replaces" in definition.description.lower()

    def test_schema_has_explicit_properties(self) -> None:
        """Todo tool schema has explicit content and status properties."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        definition = get_tool_definition(tool)
        assert definition is not None

        # Check that todos is an array
        todos_schema = definition.parameters["properties"]["todos"]
        assert todos_schema["type"] == "array"

        # Check item schema has explicit properties (not just additionalProperties)
        item_schema = todos_schema["items"]
        assert item_schema["type"] == "object"
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
    async def test_update_todo_adds_items(self) -> None:
        """Tool updates state with new items."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"content": "New task", "status": "pending"},
            ]
        )

        assert result.success
        assert len(state.items) == 1
        assert state.items[0].content == "New task"
        assert state.items[0].status == TodoStatus.PENDING

    @pytest.mark.asyncio
    async def test_update_todo_replaces_items(self) -> None:
        """Tool completely replaces existing items."""
        state = TodoState(
            items=[TodoItem(content="Old task", status=TodoStatus.PENDING)]
        )
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"content": "New task 1", "status": "in_progress"},
                {"content": "New task 2", "status": "completed"},
            ]
        )

        assert result.success
        assert len(state.items) == 2
        assert state.items[0].content == "New task 1"
        assert state.items[0].status == TodoStatus.IN_PROGRESS
        assert state.items[1].content == "New task 2"
        assert state.items[1].status == TodoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_update_todo_returns_list(self) -> None:
        """Tool returns the updated list."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"content": "Task A", "status": "pending"},
                {"content": "Task B", "status": "completed"},
            ]
        )

        assert result.success
        assert result.data == [
            {"content": "Task A", "status": "pending"},
            {"content": "Task B", "status": "completed"},
        ]

    @pytest.mark.asyncio
    async def test_invalid_status_defaults_to_pending(self) -> None:
        """Invalid status values default to pending."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"content": "Task", "status": "invalid_status"},
            ]
        )

        assert result.success
        assert state.items[0].status == TodoStatus.PENDING

    @pytest.mark.asyncio
    async def test_missing_status_defaults_to_pending(self) -> None:
        """Missing status defaults to pending."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"content": "Task without status"},
            ]
        )

        assert result.success
        assert state.items[0].status == TodoStatus.PENDING

    @pytest.mark.asyncio
    async def test_missing_content_empty_string(self) -> None:
        """Missing content becomes empty string."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        result = await tool(
            todos=[
                {"status": "pending"},
            ]
        )

        assert result.success
        assert state.items[0].content == ""

    @pytest.mark.asyncio
    async def test_state_shared_across_calls(self) -> None:
        """Same state instance is modified by multiple calls."""
        state = TodoState(items=[])
        tool = get_todo_tool(state)

        await tool(todos=[{"content": "First", "status": "pending"}])
        assert len(state.items) == 1

        await tool(
            todos=[
                {"content": "First", "status": "completed"},
                {"content": "Second", "status": "pending"},
            ]
        )
        assert len(state.items) == 2
        assert state.items[0].status == TodoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_clear_all_items(self) -> None:
        """Passing empty list clears all items."""
        state = TodoState(
            items=[
                TodoItem(content="Task 1", status=TodoStatus.PENDING),
                TodoItem(content="Task 2", status=TodoStatus.COMPLETED),
            ]
        )
        tool = get_todo_tool(state)

        result = await tool(todos=[])

        assert result.success
        assert len(state.items) == 0
        assert result.data == []
