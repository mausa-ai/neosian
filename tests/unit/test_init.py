"""Test package initialization."""

import pytest


@pytest.mark.unit
def test_version_exists() -> None:
    """Test that version is defined."""
    from neosian import __version__

    # Just check version exists and is a valid semver-like string
    assert isinstance(__version__, str)
    assert len(__version__.split(".")) >= 2


@pytest.mark.unit
def test_package_imports() -> None:
    """Test that package can be imported."""
    import neosian

    assert neosian is not None


@pytest.mark.unit
def test_public_api_exports() -> None:
    """Test that all public API items are exported."""
    from neosian import (
        Agent,
        AgentConfig,
        AgentEventType,
        AgentHooks,
        AgentResponse,
        AgentSession,
        CommonPolicies,
        Conversation,
        ConversationStore,
        Desc,
        DoneEvent,
        ErrorEvent,
        FallbackEvent,
        GuardrailErrorPolicy,
        GuardrailMode,
        GuardrailResult,
        GuardrailsConfig,
        LlmCallEvent,
        Max,
        MaxLen,
        Message,
        Min,
        MinLen,
        Model,
        Pattern,
        PolicyBuilder,
        PolicyResult,
        Provider,
        Role,
        Tool,
        ToolCall,
        ToolCallId,
        ToolEvent,
        ToolResult,
        TurnEvent,
        Usage,
        __version__,
        load_prompt,
        message_from_json,
        message_to_json,
        sse_stream,
    )

    # Verify all imports are accessible
    assert Agent is not None
    assert AgentConfig is not None
    assert AgentResponse is not None
    assert AgentSession is not None
    assert AgentHooks is not None
    assert TurnEvent is not None
    assert LlmCallEvent is not None
    assert ToolEvent is not None
    assert FallbackEvent is not None
    assert Tool is not None
    assert ToolCall is not None
    assert ToolCallId is not None
    assert ToolResult is not None
    assert Usage is not None
    assert AgentEventType is not None
    assert DoneEvent is not None
    assert ErrorEvent is not None
    assert sse_stream is not None
    assert Message is not None
    assert Role is not None
    assert message_to_json is not None
    assert message_from_json is not None
    assert Conversation is not None
    assert ConversationStore is not None
    assert Model is not None
    assert Provider is not None
    assert GuardrailsConfig is not None
    assert GuardrailMode is not None
    assert GuardrailErrorPolicy is not None
    assert GuardrailResult is not None
    assert PolicyResult is not None
    assert PolicyBuilder is not None
    assert CommonPolicies is not None
    assert load_prompt is not None
    assert __version__ is not None
    # Tool constraints
    assert Desc is not None
    assert Min is not None
    assert Max is not None
    assert MinLen is not None
    assert MaxLen is not None
    assert Pattern is not None


@pytest.mark.unit
def test_all_list_matches_exports() -> None:
    """Test that __all__ contains all expected exports."""
    import neosian

    expected = {
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
        # Models and Providers
        "AnyModel",
        "Model",
        "ModelPricing",
        "ModelSpec",
        "OpenAICompatible",
        "RegisteredModel",
        "ModelUsage",
        "MICRO_PER_USD",
        "PRICES_AS_OF",
        "PRICES_FINGERPRINT",
        "Provider",
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
        "ReasoningEffort",
        "ResponseFormat",
        "StopReason",
        "Usage",
        "format_micro_usd",
        "register_model",
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
        # Skills
        "Skill",
        "SkillName",
        "SkillLoadError",
        "load_skill",
        "load_skills",
        # Memory (DESIGN §8)
        "MemoryStore",
        "MemoryDocument",
        "MemoryEntry",
        "MemoryVersion",
        "MemoryRedaction",
        "FileStore",
        "PostgresStore",
        "RemoteStore",
        "Scope",
        "parse_scope",
        "SCOPE_PATTERN",
        "SCOPE_MAX_LENGTH",
        "Actor",
        "parse_actor",
        "ACTOR_PATTERN",
        "ACTOR_MAX_LENGTH",
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
        "AuditEntry",
        "audit",
        # Conversation (DESIGN §9)
        "Conversation",
        "ConversationStore",
        "ConversationTurn",
        "ConversationProjection",
        "ConversationView",
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
    }

    assert set(neosian.__all__) == expected
