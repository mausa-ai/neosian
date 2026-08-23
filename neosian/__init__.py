"""neosian - Async-only library for LLM agents."""

from importlib.metadata import version as _pkg_version

from neosian._foundation.agent.approval import (
    ToolApprovalRequest,
    ToolDecision,
    ToolGateConfig,
)
from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.event_schemas import event_schemas
from neosian._foundation.agent.events import (
    EVENT_PROTOCOL_VERSION,
    AgentEvent,
    AgentEventType,
    BlockedEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    EventSequencer,
    MemoryWriteEvent,
    ReadyEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    sse_stream,
)
from neosian._foundation.agent.hooks import (
    AgentHooks,
    FallbackEvent,
    LlmCallEvent,
    ToolEvent,
    TurnEvent,
)
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.blackboard.base import BlackboardProvider
from neosian._foundation.blackboard.file import FileBlackboard
from neosian._foundation.conversation.base import ConversationStore
from neosian._foundation.conversation.compaction import (
    CompactionConfig,
    CompactionResult,
)
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.conversation.reflection import (
    ReflectionConfig,
    ReflectionResult,
    ReflectionWrite,
)
from neosian._foundation.conversation.types import (
    CONVERSATION_FORMAT_VERSION,
    ConversationProjection,
    ConversationTurn,
)
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
from neosian._foundation.llm.codec import message_from_json, message_to_json
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import memory_system_section
from neosian._foundation.memory.maintenance import (
    MaintenanceResult,
    MaintenanceWrite,
    run_maintenance,
)
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.receipt import MemoryWriteReceipt
from neosian._foundation.memory.revert import revert_memory
from neosian._foundation.memory.scope import (
    SCOPE_MAX_LENGTH,
    SCOPE_PATTERN,
    Scope,
    parse_scope,
)
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.memory.types import (
    MEMORY_FORMAT_VERSION,
    MemoryDocument,
    MemoryEntry,
    MemoryVersion,
)
from neosian._foundation.postgres.store import PostgresStore
from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.shared.context_policy import ContextPolicy
from neosian._foundation.shared.exceptions import (
    ERROR_CODES,
    AgentLoadError,
    BlackboardError,
    ConfigurationError,
    ContextWindowExceededError,
    ConversationStoreError,
    EvalError,
    FallbackExhaustedError,
    GuardrailError,
    InvalidModelError,
    LLMError,
    MemoryConflictError,
    MemoryStoreError,
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
    ToolCallId,
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
    "ContextPolicy",
    # Hooks
    "AgentHooks",
    "TurnEvent",
    "LlmCallEvent",
    "ToolEvent",
    "FallbackEvent",
    # Messages
    "Message",
    "Role",
    "ContentBlock",
    "TextBlock",
    "ImageBlock",
    "DocumentBlock",
    "text_of",
    "message_to_json",
    "message_from_json",
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
    # Streaming events (v2 wire contract, DESIGN §6)
    "AgentEvent",
    "AgentEventType",
    "EVENT_PROTOCOL_VERSION",
    "EventSequencer",
    "ReadyEvent",
    "ContentEvent",
    "ReasoningEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "ToolProgressEvent",
    "MemoryWriteEvent",
    "BlockedEvent",
    "DoneEvent",
    "ErrorEvent",
    "sse_stream",
    "event_schemas",
    # Guardrails
    "GuardrailsConfig",
    "GuardrailMode",
    "GuardrailErrorPolicy",
    "GuardrailResult",
    "PolicyResult",
    "PolicyBuilder",
    "CommonPolicies",
    # Tool approval gate (DESIGN §17)
    "ToolGateConfig",
    "ToolApprovalRequest",
    "ToolDecision",
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
    # Memory (DESIGN §8)
    "MemoryStore",
    "MemoryDocument",
    "MemoryEntry",
    "MemoryVersion",
    "FileStore",
    "PostgresStore",
    "Scope",
    "parse_scope",
    "SCOPE_PATTERN",
    "SCOPE_MAX_LENGTH",
    "MEMORY_FORMAT_VERSION",
    "MemoryStoreError",
    "MemoryConflictError",
    "Mount",
    "MemoryConfig",
    "create_memory_tool",
    "memory_system_section",
    "MemoryWriteReceipt",
    "revert_memory",
    "MaintenanceResult",
    "MaintenanceWrite",
    "run_maintenance",
    # Conversation (DESIGN §9)
    "Conversation",
    "ConversationStore",
    "ConversationTurn",
    "ConversationProjection",
    "CompactionConfig",
    "CompactionResult",
    "ReflectionConfig",
    "ReflectionResult",
    "ReflectionWrite",
    "CONVERSATION_FORMAT_VERSION",
    "parse_conversation_id",
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
    "ConversationStoreError",
    "GuardrailError",
    "AgentLoadError",
    "PromptLoadError",
    "EvalError",
    "ERROR_CODES",
    # Meta
    "__version__",
]
