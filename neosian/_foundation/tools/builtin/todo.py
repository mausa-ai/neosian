"""Global todo list tool for task tracking.

Inspired by Claude Code's TodoWrite tool. Available to all agents by default.
Stateless - validates input and echoes back the list. State lives in conversation history.
"""

from typing import Literal, TypedDict

from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.types import TodoStatus
from neosian._foundation.tools.base import Tool, ToolResult


class TodoItemInput(TypedDict):
    """Input schema for a todo item.

    This TypedDict generates explicit JSON Schema with required properties,
    ensuring LLMs know exactly what keys to use.
    """

    content: str
    status: Literal["pending", "in_progress", "completed"]


@Tool(
    name=BuiltinTools.Todo.NAME,
    description=BuiltinTools.Todo.DESCRIPTION,
)
async def update_todo(
    todos: list[TodoItemInput],
) -> ToolResult[list[dict[str, str]]]:
    """Update the todo list with new items.

    Validates the input and returns the normalized list.
    The actual state lives in the conversation history.

    Args:
        todos: List of todo items with 'content' and 'status' keys.

    Returns:
        The validated todo list.
    """
    result: list[dict[str, str]] = []

    for item_dict in todos:
        content = item_dict.get("content", "")
        status_str = item_dict.get("status", "pending")

        # Validate status, default to pending if invalid
        try:
            status = TodoStatus(status_str)
        except ValueError:
            status = TodoStatus.PENDING

        result.append({"content": content, "status": status.value})

    return ToolResult.ok(result)