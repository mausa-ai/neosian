"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

from enum import Enum
from typing import NewType

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


# Todo status enum
class TodoStatus(str, Enum):
    """Status of a todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
