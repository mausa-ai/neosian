"""Built-in playbook tools for on-demand playbook discovery and loading.

Provides two tools via a factory function:
- list_playbooks: Returns names and descriptions of available playbooks.
- load_playbook: Loads a specific playbook's content by name.

Playbook data is bound via closure — no mutable state.
"""

from neosian._foundation.shared.constants import BuiltinTools, ErrorMessages
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import Playbook, PlaybookName, ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult


def create_playbook_tools(
    playbooks: list[Playbook],
) -> tuple[ToolFunction, ToolFunction]:
    """Create list_playbooks and load_playbook tools bound to the given playbooks.

    Args:
        playbooks: List of loaded Playbook instances.

    Returns:
        Tuple of (list_playbooks, load_playbook) tool functions.
    """
    playbook_map: dict[PlaybookName, Playbook] = {p.name: p for p in playbooks}

    @Tool(
        name=BuiltinTools.Playbook.LIST_NAME,
        description=get_prompt("tools.playbook_list"),
    )
    async def list_playbooks() -> ToolResult[list[dict[str, str]]]:
        """List all available playbooks with their names and descriptions."""
        return ToolResult.ok(
            [{"name": p.name, "description": p.description} for p in playbooks]
        )

    @Tool(
        name=BuiltinTools.Playbook.LOAD_NAME,
        description=get_prompt("tools.playbook_load"),
    )
    async def load_playbook(name: str) -> ToolResult[str]:
        """Load a playbook by name and return its full content.

        Args:
            name: The playbook name to load.
        """
        pb_name = PlaybookName(name)
        if pb_name not in playbook_map:
            available = ", ".join(str(p.name) for p in playbooks)
            return ToolResult.fail(
                ErrorMessages.PLAYBOOK_NOT_FOUND.format(name=name),
                system_reminder=f"Available playbooks: {available}",
            )
        return ToolResult.ok(playbook_map[pb_name].content)

    return list_playbooks, load_playbook
