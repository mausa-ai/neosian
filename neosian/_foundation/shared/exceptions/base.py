"""The root of the hierarchy: `NeosianError`, its code and its `details` (DESIGN
§5)."""

from __future__ import annotations

from typing import Any, ClassVar


class NeosianError(Exception):
    """Base exception for all neosian errors.

    Args:
        message: Developer-facing English; hosts own all user-facing text.
        details: JSON-safe structured context for logs and wire envelopes.
        retryable: Per-instance override of the class default.
    """

    code: ClassVar[str] = "neosian_error"
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.retryable = self.default_retryable if retryable is None else retryable
