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
        GuardrailErrorPolicy,
        GuardrailMode,
        GuardrailResult,
        GuardrailsConfig,
        Message,
        PolicyBuilder,
        PolicyResult,
        Role,
        Tool,
        ToolCall,
        ToolResult,
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
    assert ToolResult is not None
    assert Message is not None
    assert Role is not None
    assert GuardrailsConfig is not None
    assert GuardrailMode is not None
    assert GuardrailErrorPolicy is not None
    assert GuardrailResult is not None
    assert ClassifierResult is not None
    assert PolicyResult is not None
    assert PolicyBuilder is not None
    assert CommonPolicies is not None
    assert load_prompt is not None
    assert __version__ is not None


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
        # Tools
        "Tool",
        "ToolCall",
        "ToolResult",
        # Guardrails
        "GuardrailsConfig",
        "GuardrailMode",
        "GuardrailErrorPolicy",
        "GuardrailResult",
        "ClassifierResult",
        "PolicyResult",
        "PolicyBuilder",
        "CommonPolicies",
        # Utilities
        "load_prompt",
        # Meta
        "__version__",
    }

    assert set(neosian.__all__) == expected
