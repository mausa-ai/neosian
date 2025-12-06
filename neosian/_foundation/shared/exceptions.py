"""Exception hierarchy for neosian.

All exceptions are centralized here with their error messages.
Add exceptions as needed, not speculatively.
"""

from neosian._foundation.shared.constants import ErrorMessages


class NeosianError(Exception):
    """Base exception for all neosian errors."""

    pass


class LLMError(NeosianError):
    """Base exception for LLM-related errors."""

    pass


class ToolCallGenerationError(LLMError):
    """Raised when the LLM fails to generate a valid tool call after retries."""

    def __init__(self, retries: int) -> None:
        """Initialize with retry count.

        Args:
            retries: Number of retries attempted.
        """
        super().__init__(
            ErrorMessages.TOOL_CALL_GENERATION_FAILED.format(retries=retries)
        )
        self.retries = retries
