"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, NewType

if TYPE_CHECKING:
    from neosian._foundation.tools.base import ToolResult

# Core identifiers
AgentName = NewType("AgentName", str)
ToolName = NewType("ToolName", str)
ModelId = NewType("ModelId", str)

# Content types
SystemPrompt = NewType("SystemPrompt", str)
UserMessage = NewType("UserMessage", str)
AssistantMessage = NewType("AssistantMessage", str)
ToolCallId = NewType("ToolCallId", str)

# Provider identifiers
ProviderId = NewType("ProviderId", str)

# Tool function type (defined here to avoid circular imports)
ToolFunction = Callable[..., Awaitable["ToolResult[Any]"]]


# Todo status enum
class TodoStatus(str, Enum):
    """Status of a todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class AgentConfig:
    """Configuration for an agent.

    This is the contract for one-file agent definitions.
    Export a `configuration` variable of this type.

    Example:
        from neosian import AgentConfig, Tool, ToolResult

        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        configuration = AgentConfig(
            system_prompt="You are helpful.",
            tools=[greet],
        )
    """

    system_prompt: SystemPrompt
    tools: list[ToolFunction] = field(default_factory=list)
    provider: ProviderId | None = None
    model: ModelId | None = None
    enable_todo: bool = True
