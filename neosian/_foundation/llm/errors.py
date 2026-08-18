"""Provider SDK exception classification at the client boundary (DESIGN §5).

Every client wraps its complete() and stream() bodies with
`raise wrap_provider_error(...) from exc`, so raw SDK exceptions never
escape neosian and provider identity, HTTP status, and retryability
survive structurally to the host's logs and wire codes.
"""

from __future__ import annotations

from typing import Final

from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    NeosianError,
    ProviderError,
)
from neosian._foundation.shared.types import Model

_CONTEXT_SIGNATURES: Final = (
    "context_length_exceeded",
    "prompt is too long",
    "model_context_window_exceeded",
    "request too large",
)

# Stainless SDK + httpx transport bases, matched by class name so no SDK is
# imported here. APITimeoutError subclasses APIConnectionError in all four
# provider SDKs, so one name covers both.
_TRANSIENT_NAMES: Final = frozenset({"APIConnectionError", "TransportError"})


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _request_id_of(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    return any(base.__name__ in _TRANSIENT_NAMES for base in type(exc).__mro__)


def wrap_provider_error(
    provider: str, exc: Exception, *, model: Model | None = None
) -> NeosianError:
    """Classify an exception escaping a provider SDK call.

    Returns the error for the caller to raise (`raise wrap_provider_error(
    provider, exc) from exc` — the `from exc` keeps the SDK exception and
    traceback on `__cause__`). Never wraps a NeosianError. A 400 matching a
    context-overflow signature becomes ContextWindowExceededError when the
    model is known; 429/408/5xx/connection/timeout mark the ProviderError
    retryable.
    """
    if isinstance(exc, NeosianError):
        return exc
    status = _status_of(exc)
    if (
        status == 400
        and model is not None
        and any(sig in str(exc).lower() for sig in _CONTEXT_SIGNATURES)
    ):
        return ContextWindowExceededError(
            model.value,
            context_window=model.context_window,
            provider=provider,
        )
    if status is not None:
        retryable = status in (408, 429) or status >= 500
    else:
        retryable = _is_transient(exc)
    return ProviderError(
        provider,
        str(exc),
        status=status,
        retryable=retryable,
        request_id=_request_id_of(exc),
    )
