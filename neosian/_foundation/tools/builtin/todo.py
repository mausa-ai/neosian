"""Global todo list tool for task tracking.

Inspired by Claude Code's TodoWrite tool. Available to all agents by default.
The todo state is managed externally - the tool only processes updates.
"""

from dataclasses import dataclass

from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.types import TodoStatus
from neosian._foundation.tools.base import Tool, ToolFunction, ToolResult


@dataclass
class TodoItem:
    """A single todo item."""

    content: str
    status: TodoStatus

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for serialization."""
        return {"content": self.content, "status": self.status.value}


@dataclass
class TodoState:
    """Holds the current todo list state.

    This is passed to the tool factory to allow external state management.
    The application owns the state, neosian just provides the tool.
    """

    items: list[TodoItem]

    def to_list(self) -> list[dict[str, str]]:
        """Convert all items to list of dicts."""
        return [item.to_dict() for item in self.items]


def get_todo_tool(state: TodoState) -> ToolFunction:
    """Create a todo tool bound to the given state.

    Args:
        state: The TodoState instance to bind to. The application owns this.

    Returns:
        A tool function that can be passed to Agent.
    """

    @Tool(
        name=BuiltinTools.Todo.NAME,
        description=BuiltinTools.Todo.DESCRIPTION,
    )
    async def update_todo(
        todos: list[dict[str, str]],
    ) -> ToolResult[list[dict[str, str]]]:
        """Update the todo list with new items.

        Args:
            todos: List of todo items, each with 'content' and 'status' keys.
                   Status must be 'pending', 'in_progress', or 'completed'.

        Returns:
            The updated todo list.
        """
        # Clear and rebuild state from input
        state.items.clear()

        for item_dict in todos:
            content = item_dict.get("content", "")
            status_str = item_dict.get("status", "pending")

            try:
                status = TodoStatus(status_str)
            except ValueError:
                status = TodoStatus.PENDING

            state.items.append(TodoItem(content=content, status=status))

        return ToolResult.ok(state.to_list())

    return update_todo
