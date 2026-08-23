"""Built-in skill tools for on-demand skill discovery and loading.

Provides two tools via a factory function:
- list_skills: Returns names and descriptions of available skills.
- load_skill: Loads a specific skill's content by name.

Skill data is bound via closure — no mutable state.
"""

from neosian._foundation.shared.constants import BuiltinTools, ErrorMessages
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import Skill, SkillName, ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult


def create_skill_tools(
    skills: list[Skill],
) -> tuple[ToolFunction, ToolFunction]:
    """Create list_skills and load_skill tools bound to the given skills.

    Args:
        skills: List of loaded Skill instances.

    Returns:
        Tuple of (list_skills, load_skill) tool functions.
    """
    skill_map: dict[SkillName, Skill] = {p.name: p for p in skills}

    @Tool(
        name=BuiltinTools.Skill.LIST_NAME,
        description=get_prompt("tools.skill_list"),
    )
    async def list_skills() -> ToolResult[list[dict[str, str]]]:
        """List all available skills with their names and descriptions."""
        return ToolResult.ok(
            [{"name": p.name, "description": p.description} for p in skills]
        )

    @Tool(
        name=BuiltinTools.Skill.LOAD_NAME,
        description=get_prompt("tools.skill_load"),
    )
    async def load_skill(name: str) -> ToolResult[str]:
        """Load a skill by name and return its full content.

        Args:
            name: The skill name to load.
        """
        pb_name = SkillName(name)
        if pb_name not in skill_map:
            available = ", ".join(str(p.name) for p in skills)
            return ToolResult.fail(
                ErrorMessages.SKILL_NOT_FOUND.format(name=name),
                system_reminder=f"Available skills: {available}",
            )
        return ToolResult.ok(skill_map[pb_name].content)

    return list_skills, load_skill
