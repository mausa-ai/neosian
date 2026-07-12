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
        AgentResponse,
        AgentSession,
        CommonPolicies,
        Desc,
        EvalCase,
        EvalConfig,
        EvalResult,
        EvalTurn,
        Expectation,
        GuardrailErrorPolicy,
        GuardrailMode,
        GuardrailResult,
        GuardrailsConfig,
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
        SSEEventType,
        Tool,
        ToolCall,
        ToolCallCapture,
        ToolCallId,
        ToolResult,
        TurnResult,
        Usage,
        __version__,
        heartbeat_event,
        load_prompt,
        reasoning_event,
    )

    # Verify all imports are accessible
    assert Agent is not None
    assert AgentConfig is not None
    assert AgentResponse is not None
    assert AgentSession is not None
    assert Tool is not None
    assert ToolCall is not None
    assert ToolCallId is not None
    assert ToolResult is not None
    assert Usage is not None
    assert SSEEventType is not None
    assert heartbeat_event is not None
    assert reasoning_event is not None
    assert Message is not None
    assert Role is not None
    assert Model is not None
    assert Provider is not None
    assert GuardrailsConfig is not None
    assert GuardrailMode is not None
    assert GuardrailErrorPolicy is not None
    assert GuardrailResult is not None
    assert PolicyResult is not None
    assert PolicyBuilder is not None
    assert CommonPolicies is not None
    assert EvalConfig is not None
    assert EvalCase is not None
    assert EvalTurn is not None
    assert EvalResult is not None
    assert TurnResult is not None
    assert Expectation is not None
    assert ToolCallCapture is not None
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
        # Messages
        "Message",
        "Role",
        "ContentBlock",
        "TextBlock",
        "ImageBlock",
        "DocumentBlock",
        "text_of",
        # Models and Providers
        "Model",
        "ModelSpec",
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
        "UnsupportedContentError",
        # Meta
        "__version__",
    }

    assert set(neosian.__all__) == expected
