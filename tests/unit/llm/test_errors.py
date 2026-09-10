"""wrap_provider_error classification (DESIGN §5) — no SDK imports."""

import pytest

from neosian._foundation.llm.errors import tool_arguments, wrap_provider_error
from neosian._foundation.shared.exceptions import (
    AuthenticationError,
    ContextWindowExceededError,
    ProviderError,
    UnsupportedContentError,
)
from neosian._foundation.shared.types import Model


class _StatusError(Exception):
    """Stub mimicking a Stainless APIStatusError."""

    def __init__(
        self, message: str, *, status_code: int, request_id: str | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        if request_id is not None:
            self.request_id = request_id


class _Response:
    def __init__(self, status_code: object) -> None:
        self.status_code = status_code


class _ResponseError(Exception):
    """Stub carrying status only on .response, like some transport errors."""

    def __init__(self, message: str, *, response: _Response) -> None:
        super().__init__(message)
        self.response = response


class APIConnectionError(Exception):
    """Name-matched transient stub (the wrap matches by MRO class name)."""


class APITimeoutError(APIConnectionError):
    pass


@pytest.mark.unit
class TestWrapProviderError:
    def test_neosian_error_passes_through_unchanged(self) -> None:
        original = UnsupportedContentError("images not supported")
        assert wrap_provider_error("cerebras", original) is original

    def test_status_code_extraction(self) -> None:
        wrapped = wrap_provider_error("cerebras", _StatusError("boom", status_code=404))
        assert isinstance(wrapped, ProviderError)
        assert wrapped.status == 404

    def test_status_falls_back_to_response(self) -> None:
        exc = _ResponseError("boom", response=_Response(503))
        wrapped = wrap_provider_error("cerebras", exc)
        assert isinstance(wrapped, ProviderError)
        assert wrapped.status == 503
        assert wrapped.retryable is True

    def test_non_int_status_is_none(self) -> None:
        exc = _ResponseError("boom", response=_Response("teapot"))
        wrapped = wrap_provider_error("cerebras", exc)
        assert isinstance(wrapped, ProviderError)
        assert wrapped.status is None
        assert wrapped.retryable is False

    @pytest.mark.parametrize("status", [408, 429, 500, 503, 529])
    def test_retryable_statuses(self, status: int) -> None:
        wrapped = wrap_provider_error("cerebras", _StatusError("x", status_code=status))
        assert wrapped.retryable is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_non_retryable_statuses(self, status: int) -> None:
        wrapped = wrap_provider_error("cerebras", _StatusError("x", status_code=status))
        assert wrapped.retryable is False

    @pytest.mark.parametrize(
        "exc",
        [
            APIConnectionError("connect refused"),
            APITimeoutError("timed out"),
            TimeoutError("socket timeout"),
            ConnectionError("reset by peer"),
        ],
    )
    def test_transient_statusless_errors_are_retryable(self, exc: Exception) -> None:
        wrapped = wrap_provider_error("openai", exc)
        assert isinstance(wrapped, ProviderError)
        assert wrapped.retryable is True

    def test_unknown_statusless_error_not_retryable(self) -> None:
        wrapped = wrap_provider_error("openai", ValueError("garbled payload"))
        assert isinstance(wrapped, ProviderError)
        assert wrapped.retryable is False

    @pytest.mark.parametrize(
        "signature",
        [
            "context_length_exceeded",
            "prompt is too long",
            "model_context_window_exceeded",
        ],
    )
    def test_context_signature_at_400_with_model(self, signature: str) -> None:
        exc = _StatusError(f"error: {signature}", status_code=400)
        wrapped = wrap_provider_error("anthropic", exc, model=Model.CLAUDE_HAIKU_4_5)
        assert isinstance(wrapped, ContextWindowExceededError)
        assert wrapped.model == Model.CLAUDE_HAIKU_4_5.value
        assert wrapped.context_window == Model.CLAUDE_HAIKU_4_5.context_window
        assert wrapped.provider == "anthropic"

    def test_context_signature_at_413_with_model(self) -> None:
        """A door that rejects an oversize prompt as 413 is still an overflow."""
        exc = _StatusError("context_length_exceeded", status_code=413)
        wrapped = wrap_provider_error("gemini", exc, model=Model.CLAUDE_HAIKU_4_5)
        assert isinstance(wrapped, ContextWindowExceededError)

    def test_a_429_request_too_large_is_not_retryable(self) -> None:
        """OpenAI's per-request TPM cap (LL-19): a retry can never clear it,
        and it is not a context overflow — the fallback gate stays out."""
        exc = _StatusError(
            "Request too large for gpt-5 on tokens per min (TPM)", status_code=429
        )
        wrapped = wrap_provider_error("openai", exc, model=Model.GPT_5_6_LUNA)
        assert type(wrapped) is ProviderError
        assert wrapped.status == 429
        assert wrapped.retryable is False

    def test_context_signature_at_500_stays_provider_error(self) -> None:
        exc = _StatusError("prompt is too long", status_code=500)
        wrapped = wrap_provider_error("anthropic", exc, model=Model.CLAUDE_HAIKU_4_5)
        assert isinstance(wrapped, ProviderError)

    def test_context_signature_without_model_stays_provider_error(self) -> None:
        exc = _StatusError("prompt is too long", status_code=400)
        wrapped = wrap_provider_error("anthropic", exc)
        assert isinstance(wrapped, ProviderError)

    def test_request_id_pickup(self) -> None:
        exc = _StatusError("boom", status_code=500, request_id="req_123")
        wrapped = wrap_provider_error("anthropic", exc)
        assert isinstance(wrapped, ProviderError)
        assert wrapped.request_id == "req_123"
        wrapped_without = wrap_provider_error(
            "anthropic", _StatusError("boom", status_code=500)
        )
        assert isinstance(wrapped_without, ProviderError)
        assert wrapped_without.request_id is None

    def test_provider_and_message_preserved(self) -> None:
        wrapped = wrap_provider_error("cerebras", ValueError("bad chunk"))
        assert isinstance(wrapped, ProviderError)
        assert wrapped.provider == "cerebras"
        assert "bad chunk" in wrapped.message


@pytest.mark.unit
class TestToolArguments:
    """Streamed tool-call arguments decode in one place (LL-7)."""

    def test_empty_is_an_empty_object(self) -> None:
        assert tool_arguments("openai", "", stop_reason=None) == {}

    def test_a_non_object_normalises_to_empty(self) -> None:
        assert tool_arguments("openai", "[1]", stop_reason="stop") == {}

    def test_an_object_decodes(self) -> None:
        assert tool_arguments("openai", '{"a": 1}', stop_reason="stop") == {"a": 1}

    def test_truncated_json_names_the_stop_reason(self) -> None:
        with pytest.raises(ProviderError) as exc_info:
            tool_arguments("anthropic", '{"location":', stop_reason="max_tokens")
        error = exc_info.value
        assert error.provider == "anthropic"
        assert "stop reason: max_tokens" in str(error)
        assert error.retryable is False
        assert isinstance(error.__cause__, ValueError)

    def test_an_unknown_stop_reason_is_named_as_such(self) -> None:
        with pytest.raises(ProviderError, match="stop reason: unknown"):
            tool_arguments("cerebras", "{", stop_reason=None)


@pytest.mark.unit
class TestAuthentication:
    """A 401/403 is `AuthenticationError` — a ProviderError, never
    retryable, still fallback-eligible (NF #171, LL-20)."""

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials(self, status: int) -> None:
        error = wrap_provider_error(
            "openai", _StatusError("bad key", status_code=status, request_id="r1")
        )
        assert isinstance(error, AuthenticationError)
        assert isinstance(error, ProviderError)
        assert error.code == "llm_authentication_failed"
        assert error.retryable is False
        assert error.status == status
        assert error.request_id == "r1"
        assert error.details == {
            "provider": "openai",
            "status": status,
            "request_id": "r1",
        }
