"""Provider failures, the context window, the fake's script, and fallback
exhaustion (DESIGN §5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.llm import LLMError

# Three rungs or more: the two-model sentence cannot say what happened,
# so the ladder lists itself (NC9, ledger #223).
_LADDER_EXHAUSTED = "All {count} models in the fallback ladder failed. {failures}"

if TYPE_CHECKING:
    from neosian._foundation.llm.base import ModelUsage, Usage


class ProviderError(LLMError):
    """Raised when a provider fails (rate limit, auth, network, etc.).

    Produced by wrap_provider_error at every client boundary; provider
    identity and HTTP status are preserved structurally, never stringified
    away (ECOSYSTEM §6). This error triggers fallback to the next provider
    in the chain.
    """

    code = "llm_provider_error"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        """Initialize with provider and error details.

        Args:
            provider: Provider identifier (e.g., "openai", "anthropic").
            message: Error description from the provider SDK.
            status: HTTP status code, when the failure carried one.
            retryable: Whether the failure class is worth retrying
                (429/408/5xx/connection/timeout).
            request_id: Provider request id, when the SDK exposed one.
        """
        super().__init__(
            ErrorMessages.PROVIDER_FAILED.format(provider=provider, error=message),
            details={"provider": provider, "status": status, "request_id": request_id},
            retryable=retryable,
        )
        self.provider = provider
        self.status = status
        self.request_id = request_id


class AuthenticationError(ProviderError):
    """The provider rejected the credentials (HTTP 401/403).

    Never retryable — a wrong key stays wrong — and fallback-eligible like
    any ProviderError, so a second provider with its own key still answers
    (NF #171, LL-20).
    """

    code = "llm_authentication_failed"


class ContextWindowExceededError(LLMError):
    """Raised when a request cannot fit the model's context window.

    Reactive producer: wrap_provider_error classifying a provider 400.
    Proactive producer: ContextPolicy (N0). An overflow does not trigger
    fallback unless the fallback model's window exceeds the primary's —
    falling back smaller is a guaranteed second failure and a doubled bill.
    """

    code = "llm_context_window_exceeded"

    def __init__(
        self,
        model: str,
        *,
        context_window: int | None = None,
        estimated_tokens: int | None = None,
        provider: str | None = None,
    ) -> None:
        message = f"Context window exceeded for model {model}"
        if context_window is not None:
            message += f" (window: {context_window} tokens)"
        super().__init__(
            message,
            details={
                "model": model,
                "context_window": context_window,
                "estimated_tokens": estimated_tokens,
                "provider": provider,
            },
        )
        self.model = model
        self.context_window = context_window
        self.estimated_tokens = estimated_tokens
        self.provider = provider


class FakeScriptExhaustedError(LLMError):
    """Raised when a FakeClient's script runs out of turns (neosian.fake).

    Lives here, not in llm/fake.py, so the ERROR_CODES subclass walk is
    import-order-deterministic.
    """

    code = "llm_fake_script_exhausted"

    def __init__(self, consumed: int) -> None:
        super().__init__(
            f"FakeScript exhausted after {consumed} turn(s)",
            details={"consumed": consumed},
        )
        self.consumed = consumed


class ModelFailedError(LLMError):
    """Raised when the configured model fails.

    When has_fallback is False, this indicates no fallback was configured
    and the caller should handle the failure appropriately.
    """

    code = "llm_model_failed"

    def __init__(
        self,
        model: str,
        error: str,
        *,
        has_fallback: bool,
        usage: Usage | None = None,
        usage_by_model: tuple[ModelUsage, ...] = (),
        cause_code: str | None = None,
        provider_status: int | None = None,
    ) -> None:
        """Initialize with model and error details.

        Args:
            model: Model identifier that failed.
            error: Error description.
            has_fallback: True if a fallback is configured (failure is recoverable),
                False if no fallback exists (this is the final error).
            usage: Best-effort token usage billed before the failure.
            usage_by_model: Per-API-reported-model split of `usage`.
            cause_code: Machine code of the underlying failure, so terminal
                frames carry structure, not formatted English.
            provider_status: HTTP status of the underlying failure, if any.
        """
        if has_fallback:
            message = ErrorMessages.MODEL_FAILED.format(model=model, error=error)
        else:
            message = ErrorMessages.MODEL_FAILED_NO_FALLBACK.format(
                model=model, error=error
            )
        super().__init__(
            message,
            details={
                "model": model,
                "error": error,
                "has_fallback": has_fallback,
                "cause_code": cause_code,
                "provider_status": provider_status,
            },
            usage=usage,
            usage_by_model=usage_by_model,
        )
        self.model = model
        self.error = error
        self.has_fallback = has_fallback
        self.cause_code = cause_code
        self.provider_status = provider_status


class FallbackExhaustedError(LLMError):
    """Raised when both main and fallback models have failed."""

    code = "llm_fallback_exhausted"

    def __init__(
        self,
        main_model: str,
        main_error: str,
        fallback_model: str,
        fallback_error: str,
        *,
        usage: Usage | None = None,
        usage_by_model: tuple[ModelUsage, ...] = (),
        attempts: tuple[tuple[str, str], ...] = (),
        cause_code: str | None = None,
        provider_status: int | None = None,
    ) -> None:
        """Initialize with details from every failed model.

        Args:
            main_model: The first model tried.
            main_error: Error from the first model.
            fallback_model: The last model tried, which also failed.
            fallback_error: Error from the last model.
            attempts: Every (model, error) in the order they were tried —
                the whole ladder when a run walked more than two rungs
                (NC9, ledger #223). Empty means the two above are all
                there was.
            usage: Best-effort combined token usage billed across both
                failed attempts.
            usage_by_model: Per-API-reported-model split of `usage`.
            cause_code: Machine code of the final underlying failure, so
                terminal frames carry structure, not formatted English.
            provider_status: HTTP status of the final failure, if any.
        """
        attempts = attempts or (
            (main_model, main_error),
            (fallback_model, fallback_error),
        )
        message = (
            ErrorMessages.FALLBACK_EXHAUSTED.format(
                main_model=main_model,
                main_error=main_error,
                fallback_model=fallback_model,
                fallback_error=fallback_error,
            )
            if len(attempts) == 2
            else _LADDER_EXHAUSTED.format(
                count=len(attempts),
                failures="; ".join(f"{m}: {e}" for m, e in attempts),
            )
        )
        super().__init__(
            message,
            details={
                "main_model": main_model,
                "main_error": main_error,
                "fallback_model": fallback_model,
                "fallback_error": fallback_error,
                "attempts": [list(pair) for pair in attempts],
                "cause_code": cause_code,
                "provider_status": provider_status,
            },
            usage=usage,
            usage_by_model=usage_by_model,
        )
        self.main_model = main_model
        self.main_error = main_error
        self.fallback_model = fallback_model
        self.fallback_error = fallback_error
        self.attempts = attempts
        self.cause_code = cause_code
        self.provider_status = provider_status
