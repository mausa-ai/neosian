"""Global todo list tool for task tracking.

Inspired by Claude Code's TodoWrite tool. Available to all agents by default.
Stateless - validates input and echoes back the list. State lives in conversation history.
"""

from typing import Literal, TypedDict

from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import TodoStatus
from neosian._foundation.tools.base import Tool, ToolResult

_INVALID_STATUS = "todos[{index}]: status '{status}' is not one of {legal}"


# One item; a TypedDict so the schema states the keys (no docstring — a
# class docstring would reach the model, and its prose lives in YAML).
class TodoItemInput(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]


@Tool(
    name=BuiltinTools.Todo.NAME,
    description=get_prompt("tools.todo"),
    params=get_prompt_params("tools.todo_params"),
)
async def update_todo(
    todos: list[TodoItemInput],
) -> ToolResult[list[dict[str, str]]]:
    """Validate the list and echo it back; the state lives in the
    conversation history."""
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
