"""Type definitions for neosian.

All NewType definitions are centralized here.
"""

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
