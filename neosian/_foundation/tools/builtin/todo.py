"""Global todo list tool for task tracking.

Inspired by Claude Code's TodoWrite tool. Available to all agents by default.
Stateless - validates input and echoes back the list. State lives in conversation history.
"""

from typing import Literal, TypedDict

from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import TodoStatus
from neosian._foundation.tools.base import Tool, ToolResult

_INVALID_STATUS = "todos[{index}]: status '{status}' is not one of {legal}"


class TodoItemInput(TypedDict):
    """Input schema for a todo item.

    This TypedDict generates explicit JSON Schema with required properties,
    ensuring LLMs know exactly what keys to use.
    """

    content: str
    status: Literal["pending", "in_progress", "completed"]


@Tool(
    name=BuiltinTools.Todo.NAME,
    description=get_prompt("tools.todo"),
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

    for index, item_dict in enumerate(todos):
        content = item_dict.get("content", "")
        status_str = item_dict.get("status", "pending")
        try:
            status = TodoStatus(status_str)
        except ValueError:
            return ToolResult.fail(
                _INVALID_STATUS.format(
                    index=index,
                    status=status_str,
                    legal=", ".join(member.value for member in TodoStatus),
                )
            )
        result.append({"content": content, "status": status.value})

    return ToolResult.ok(result)
