"""neosian - Stateless agentic AI library."""

from neosian._foundation.agent.base import Agent, AgentResponse
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.agent.streaming import (
    SSEEventType,
    error_event,
    heartbeat_event,
    reasoning_event,
)
from neosian._foundation.guardrails.policy import CommonPolicies, PolicyBuilder
from neosian._foundation.llm.base import Message, Role, ToolCall, Usage
from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.blackboard.file import FileBlackboard
from neosian._foundation.shared.exceptions import (
    BlackboardError,
    FallbackExhaustedError,
    InvalidModelError,
    MessageSerializationError,
    ModelFailedError,
    PlaybookLoadError,
    StructuredOutputError,
    StructuredOutputStreamingError,
    StructuredOutputToolsError,
)
from neosian._foundation.shared.playbook import load_playbook, load_playbooks
from neosian._foundation.shared.prompt import load_prompt
from neosian._foundation.shared.types import (
    AgentConfig,
    BlackboardEntry,
    BlackboardName,
    EvalCase,
    EvalConfig,
    EvalResult,
    EvalTurn,
    Expectation,
    FallbackConfig,
    FallbackState,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailResult,
    GuardrailsConfig,
    Model,
    ModelSpec,
    Playbook,
    PlaybookName,
    PolicyResult,
    Provider,
    ReasoningEffort,
    ResponseFormat,
    ToolCallCapture,
    ToolCallId,
    TurnResult,
)
from neosian._foundation.tools.base import Tool, ToolResult

__version__ = "0.47.0"

__all__ = [
    # Agent
    "Agent",
    "AgentConfig",
    "AgentResponse",
    "AgentSession",
    # Messages
    "Message",
    "Role",
    # Tools
    "Tool",
    "ToolCall",
    "ToolCallId",
    "ToolResult",
    # Tool Constraints
    "Desc",
    "Min",
    "Max",
    "MinLen",
    "MaxLen",
    "Pattern",
    # LLM
    "Model",
    "ModelSpec",
    "Provider",
    "ReasoningEffort",
    "ResponseFormat",
    "Usage",
    # Streaming
    "SSEEventType",
    "error_event",
    "heartbeat_event",
    "reasoning_event",
    # Guardrails
    "GuardrailsConfig",
    "GuardrailMode",
    "GuardrailErrorPolicy",
    "GuardrailResult",
    "PolicyResult",
    "PolicyBuilder",
    "CommonPolicies",
    # Evaluation
    "EvalConfig",
    "EvalCase",
    "EvalTurn",
    "EvalResult",
    "TurnResult",
    "Expectation",
    "ToolCallCapture",
    # Playbooks
    "Playbook",
    "PlaybookName",
    "PlaybookLoadError",
    "load_playbook",
    "load_playbooks",
    # Blackboard
    "BlackboardProvider",
    "BlackboardEntry",
    "BlackboardName",
    "FileBlackboard",
    "BlackboardError",
    # Utilities
    "load_prompt",
    # Fallback
    "FallbackConfig",
    "FallbackState",
    # Exceptions
    "ModelFailedError",
    "FallbackExhaustedError",
    "InvalidModelError",
    "MessageSerializationError",
    "StructuredOutputError",
    "StructuredOutputStreamingError",
    "StructuredOutputToolsError",
    # Meta
    "__version__",
]
