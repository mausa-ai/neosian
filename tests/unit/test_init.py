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
        ClassifierResult,
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
        load_prompt,
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
    assert Message is not None
    assert Role is not None
    assert Model is not None
    assert Provider is not None
    assert GuardrailsConfig is not None
    assert GuardrailMode is not None
    assert GuardrailErrorPolicy is not None
    assert GuardrailResult is not None
    assert ClassifierResult is not None
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
        # Models and Providers
        "Model",
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
        "Usage",
        # Streaming
        "SSEEventType",
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
        # Exceptions
        "InvalidModelError",
        # Meta
        "__version__",
    }

    assert set(neosian.__all__) == expected
