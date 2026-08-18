"""Built-in blackboard tools for dynamic context reading and updating.

Provides three tools via a factory function:
- list_blackboard: Returns names and descriptions of available entries.
- read_blackboard: Reads a specific entry's current content.
- update_blackboard: Updates an existing entry's content.

The BlackboardProvider is bound via closure.
"""

from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.shared.constants import BuiltinTools, ErrorMessages
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult


def create_blackboard_tools(
    provider: BlackboardProvider,
) -> tuple[ToolFunction, ToolFunction, ToolFunction]:
    """Create list, read, and update blackboard tools bound to a provider.

    Args:
        provider: The blackboard provider instance.

    Returns:
        Tuple of (list_blackboard, read_blackboard, update_blackboard) tool functions.
    """

    @Tool(
        name=BuiltinTools.Blackboard.LIST_NAME,
        description=get_prompt("tools.blackboard_list"),
    )
    async def list_blackboard() -> ToolResult[list[dict[str, str]]]:
        """List all available blackboard entries."""
        entries = await provider.list_entries()
        return ToolResult.ok(
            [{"name": e.name, "description": e.description} for e in entries]
        )

    @Tool(
        name=BuiltinTools.Blackboard.READ_NAME,
        description=get_prompt("tools.blackboard_read"),
    )
    async def read_blackboard(name: str) -> ToolResult[str]:
        """Read the current content of a blackboard entry.

        Args:
            name: The entry name to read.
        """
        content = await provider.read_entry(name)
        if content is None:
            # Get available entries for helpful error
            entries = await provider.list_entries()
            available = ", ".join(e.name for e in entries)
            return ToolResult.fail(
                ErrorMessages.BLACKBOARD_ENTRY_NOT_FOUND.format(name=name),
                system_reminder=f"Available entries: {available}",
            )
        return ToolResult.ok(content)

    @Tool(
        name=BuiltinTools.Blackboard.UPDATE_NAME,
        description=get_prompt("tools.blackboard_update"),
    )
    async def update_blackboard(name: str, content: str) -> ToolResult[str]:
        """Update an existing blackboard entry with new content.

        Args:
            name: The entry name to update.
            content: The new content.
        """
        success = await provider.update_entry(name, content)
        if not success:
            entries = await provider.list_entries()
            available = ", ".join(e.name for e in entries)
            return ToolResult.fail(
                ErrorMessages.BLACKBOARD_ENTRY_NOT_FOUND.format(name=name),
                system_reminder=f"Available entries: {available}",
            )
        return ToolResult.ok(f"Updated '{name}'")

    return list_blackboard, read_blackboard, update_blackboard
