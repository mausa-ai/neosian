"""neosian - Stateless agentic AI library."""

from importlib.metadata import version as _pkg_version

from neosian._foundation.agent.base import Agent, AgentResponse
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.agent.streaming import (
    SSEEventType,
    error_event,
    heartbeat_event,
    reasoning_event,
)
from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.blackboard.file import FileBlackboard
from neosian._foundation.guardrails.policy import CommonPolicies, PolicyBuilder
from neosian._foundation.llm.base import (
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    ModelUsage,
    Role,
    StopReason,
    TextBlock,
    ToolCall,
    Usage,
    normalize_stop_reason,
    text_of,
)
from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.shared.exceptions import (
    ERROR_CODES,
    AgentLoadError,
    BlackboardError,
    ConfigurationError,
    ContextWindowExceededError,
    EvalError,
    FallbackExhaustedError,
    GuardrailError,
    InvalidModelError,
    LLMError,
    MessageSerializationError,
    MissingAPIKeyError,
    ModelFailedError,
    NeosianError,
    PlaybookLoadError,
    PromptLoadError,
    ProviderError,
    StructuredOutputError,
    StructuredOutputStreamingError,
    StructuredOutputToolsError,
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.playbook import load_playbook, load_playbooks
from neosian._foundation.shared.prompt import load_prompt
from neosian._foundation.shared.types import (
    MICRO_PER_USD,
    PRICES_AS_OF,
    PRICES_FINGERPRINT,
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
    ModelPricing,
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
    format_micro_usd,
)
from neosian._foundation.tools.base import Tool, ToolResult

__version__ = _pkg_version("neosian")

__all__ = [
    # Agent
    "Agent",
    "AgentConfig",
    "AgentResponse",
    "AgentSession",
    # Messages
    "Message",
    "Role",
    "ContentBlock",
    "TextBlock",
    "ImageBlock",
    "DocumentBlock",
    "text_of",
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
    "ModelPricing",
    "ModelSpec",
    "ModelUsage",
    "MICRO_PER_USD",
    "PRICES_AS_OF",
    "PRICES_FINGERPRINT",
    "Provider",
    "ReasoningEffort",
    "ResponseFormat",
    "StopReason",
    "Usage",
    "format_micro_usd",
    "normalize_stop_reason",
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
    "NeosianError",
    "LLMError",
    "ProviderError",
    "ContextWindowExceededError",
    "ModelFailedError",
    "FallbackExhaustedError",
    "ToolCallGenerationError",
    "ConfigurationError",
    "InvalidModelError",
    "MissingAPIKeyError",
    "MessageSerializationError",
    "StructuredOutputError",
    "StructuredOutputStreamingError",
    "StructuredOutputToolsError",
    "UnsupportedContentError",
    "UnsupportedParameterError",
    "GuardrailError",
    "AgentLoadError",
    "PromptLoadError",
    "EvalError",
    "ERROR_CODES",
    # Meta
    "__version__",
]
