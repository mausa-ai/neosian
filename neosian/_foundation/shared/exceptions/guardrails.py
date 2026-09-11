"""Guardrail policy and streaming errors (DESIGN §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError

if TYPE_CHECKING:
    from neosian._foundation.llm.base import Usage


class GuardrailError(NeosianError):
    """Base exception for guardrail-related errors."""

    code = "guardrail_error"


class GuardrailPolicyParseError(GuardrailError):
    """Raised when policy response cannot be parsed."""

    code = "guardrail_policy_parse_failed"

    def __init__(
        self,
        response: str,
        *,
        usage: Usage | None = None,
        api_model: str | None = None,
    ) -> None:
        """`usage`/`api_model` carry the billed classifier call so the run's
        ledger still records it — never undercount (NF #171, NQ's carry)."""
        super().__init__(
            ErrorMessages.GUARDRAIL_POLICY_PARSE_ERROR.format(response=response),
            details={"response_chars": len(response), "api_model": api_model},
        )
        self.response = response
        self.usage = usage
        self.api_model = api_model


class GuardrailStreamingError(GuardrailError):
    """Raised when streaming is requested with output guardrails configured."""

    code = "guardrail_output_requires_blocking"

    def __init__(self) -> None:
        super().__init__(ErrorMessages.GUARDRAIL_OUTPUT_REQUIRES_BLOCKING)
