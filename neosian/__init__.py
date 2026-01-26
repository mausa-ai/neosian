"""neosian - Stateless agentic AI library."""

from neosian._foundation.agent.base import Agent, AgentResponse
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.agent.streaming import (
    SSEEventType,
    error_event,
    heartbeat_event,
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
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    InvalidModelError,
    ModelFailedError,
)
from neosian._foundation.shared.prompt import load_prompt
from neosian._foundation.shared.types import (
    AgentConfig,
    ClassifierResult,
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
    PolicyResult,
    Provider,
    ToolCallCapture,
    ToolCallId,
    TurnResult,
)
from neosian._foundation.tools.base import Tool, ToolResult

__version__ = "0.25.0"

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
    "Provider",
    "Usage",
    # Streaming
    "SSEEventType",
    "error_event",
    "heartbeat_event",
    # Guardrails
    "GuardrailsConfig",
    "GuardrailMode",
    "GuardrailErrorPolicy",
    "GuardrailResult",
    "ClassifierResult",
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
    # Utilities
    "load_prompt",
    # Fallback
    "FallbackConfig",
    "FallbackState",
    # Exceptions
    "ModelFailedError",
    "FallbackExhaustedError",
    "InvalidModelError",
    # Meta
    "__version__",
]
