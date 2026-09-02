"""Provider SDK exception classification at the client boundary (DESIGN §5).

Every client wraps its complete() and stream() bodies with
`raise wrap_provider_error(...) from exc`, so raw SDK exceptions never
escape neosian and provider identity, HTTP status, and retryability
survive structurally to the host's logs and wire codes.
"""

from __future__ import annotations

import json
from typing import Any, Final

from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    NeosianError,
    ProviderError,
)
from neosian._foundation.shared.types import AnyModel

_CONTEXT_SIGNATURES: Final = (
    "context_length_exceeded",
    "prompt is too long",
    "model_context_window_exceeded",
)

# OpenAI's per-request tokens-per-minute cap arrives as a 429 — a retry can
# never clear it, and it is not a context overflow (LL-19).
_REQUEST_TOO_LARGE: Final = "request too large"

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
    provider: str, exc: Exception, *, model: AnyModel | None = None
) -> NeosianError:
    """Classify an exception escaping a provider SDK call.

    Returns the error for the caller to raise (`raise wrap_provider_error(
    provider, exc) from exc` — the `from exc` keeps the SDK exception and
    traceback on `__cause__`). Never wraps a NeosianError. A 400 or 413
    matching a context-overflow signature becomes ContextWindowExceededError
    when the model is known; 429/408/5xx/connection/timeout mark the
    ProviderError retryable, except a 429 that says the request itself is
    too large.
    """
    if isinstance(exc, NeosianError):
        return exc
    status = _status_of(exc)
    text = str(exc).lower()
    if (
        status in (400, 413)
        and model is not None
        and any(sig in text for sig in _CONTEXT_SIGNATURES)
    ):
        return ContextWindowExceededError(
            model.value,
            context_window=model.context_window,
            provider=provider,
        )
    if status == 429 and _REQUEST_TOO_LARGE in text:
        retryable = False
    elif status is not None:
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


def tool_arguments(
    provider: str, raw: str, *, stop_reason: str | None
) -> dict[str, Any]:
    """Decode streamed tool-call arguments; empty is `{}`.

    A body that is not JSON was cut short, and the error names the
    provider's stop reason (`max_tokens`, `length`) so the caller sees the
    truncation, not the decoder (LL-7).
    """
    try:
        arguments = json.loads(raw or "{}")
    except ValueError as exc:
        raise ProviderError(
            provider,
            "tool call arguments are not valid JSON "
            f"(stop reason: {stop_reason or 'unknown'}): {exc}",
        ) from exc
    return arguments if isinstance(arguments, dict) else {}
