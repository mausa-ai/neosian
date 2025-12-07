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
        ClassifierResult,
        CommonPolicies,
        GuardrailMode,
        GuardrailResult,
        GuardrailsConfig,
        PolicyBuilder,
        PolicyResult,
        Tool,
        ToolResult,
        __version__,
    )

    # Verify all imports are accessible
    assert Agent is not None
    assert AgentConfig is not None
    assert AgentResponse is not None
    assert Tool is not None
    assert ToolResult is not None
    assert GuardrailsConfig is not None
    assert GuardrailMode is not None
    assert GuardrailResult is not None
    assert ClassifierResult is not None
    assert PolicyResult is not None
    assert PolicyBuilder is not None
    assert CommonPolicies is not None
    assert __version__ is not None


@pytest.mark.unit
def test_all_list_matches_exports() -> None:
    """Test that __all__ contains all expected exports."""
    import neosian

    expected = {
        "Agent",
        "AgentConfig",
        "AgentResponse",
        "Tool",
        "ToolResult",
        "GuardrailsConfig",
        "GuardrailMode",
        "GuardrailResult",
        "ClassifierResult",
        "PolicyResult",
        "PolicyBuilder",
        "CommonPolicies",
        "__version__",
    }

    assert set(neosian.__all__) == expected
