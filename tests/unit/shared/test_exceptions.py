"""Tests for exceptions."""

import pytest

from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    LLMError,
    NeosianError,
    ToolCallGenerationError,
)


@pytest.mark.unit
class TestNeosianError:
    """Test base exception."""

    def test_can_raise_and_catch(self) -> None:
        """NeosianError should be raisable and catchable."""
        with pytest.raises(NeosianError):
            raise NeosianError("test error")

    def test_inherits_from_exception(self) -> None:
        """NeosianError should inherit from Exception."""
        assert issubclass(NeosianError, Exception)


@pytest.mark.unit
class TestLLMError:
    """Test LLM base exception."""

    def test_inherits_from_neosian_error(self) -> None:
        """LLMError should inherit from NeosianError."""
        assert issubclass(LLMError, NeosianError)

    def test_can_catch_as_neosian_error(self) -> None:
        """LLMError should be catchable as NeosianError."""
        with pytest.raises(NeosianError):
            raise LLMError("llm error")


@pytest.mark.unit
class TestToolCallGenerationError:
    """Test tool call generation error."""

    def test_inherits_from_llm_error(self) -> None:
        """ToolCallGenerationError should inherit from LLMError."""
        assert issubclass(ToolCallGenerationError, LLMError)

    def test_stores_retry_count(self) -> None:
        """Should store the retry count."""
        error = ToolCallGenerationError(retries=3)
        assert error.retries == 3

    def test_message_includes_retry_count(self) -> None:
        """Error message should include retry count."""
        error = ToolCallGenerationError(retries=2)
        assert "2" in str(error)
        assert "retries" in str(error).lower()

    def test_uses_constant_in_message(self) -> None:
        """Should use the centralized error message format."""
        error = ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)
        assert str(LLMDefaults.MAX_TOOL_CALL_RETRIES) in str(error)
