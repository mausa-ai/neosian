"""The SDK-wrap boundary on all four clients (DESIGN §5).

Raw SDK exceptions must never escape complete() or stream(); they surface
as ProviderError (or ContextWindowExceededError) with `__cause__` intact.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cerebras.cloud.sdk.types.chat.chat_completion import ChatChunkResponse

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolDefinition,
)
from neosian._foundation.llm.cerebras import CerebrasClient
from neosian._foundation.llm.openai import OpenAIClient, OpenAICompatibleClient
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    ProviderError,
)
from neosian._foundation.shared.types import Model, OpenAICompatible, ToolName


class _ServerError(Exception):
    """Stub SDK failure carrying an HTTP status."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _ChunkIterator:
    def __init__(self, items: list[object], error: Exception | None = None) -> None:
        self._items = iter(items)
        self._error = error

    def __aiter__(self) -> "_ChunkIterator":
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._items)
        except StopIteration:
            if self._error is not None:
                raise self._error from None
            raise StopAsyncIteration from None


def _content_chunk(text: str, spec: type | None = None) -> MagicMock:
    chunk = MagicMock(spec=spec) if spec is not None else MagicMock()
    chunk.model = "stream-model"
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.choices[0].delta.reasoning = None
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    return chunk


_XAI = OpenAICompatible(name="xai", api_key_env="XAI_API_KEY")


def _xai_client(api_key: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_key, door=_XAI)


# (client factory, provider name, model) for the OpenAI-compatible wire:
# Cerebras on its own SDK, OpenAI on its door, a registered door.
_OPENAI_COMPAT = [
    (CerebrasClient, "cerebras", Model.CEREBRAS_GPT_OSS_120B),
    (OpenAIClient, "openai", Model.GPT_5_NANO),
    (_xai_client, "xai", Model.GPT_5_NANO),
]


def _sdk(client: BaseLLMClient) -> Any:
    return client._client  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("client_cls", "provider", "model"),
    _OPENAI_COMPAT,
    ids=[p for _, p, _ in _OPENAI_COMPAT],
)
class TestOpenAICompatWrap:
    async def test_complete_wraps_server_error(
        self, client_cls: type[BaseLLMClient], provider: str, model: Model
    ) -> None:
        client = client_cls(api_key="test-key")  # type: ignore[call-arg]
        original = _ServerError("upstream exploded", status_code=500)
        _sdk(client).chat.completions.create = AsyncMock(side_effect=original)

        with pytest.raises(ProviderError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")], model=model
            )
        assert exc_info.value.provider == provider
        assert exc_info.value.status == 500
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is original

    async def test_stream_initial_error_wraps_on_first_anext(
        self, client_cls: type[BaseLLMClient], provider: str, model: Model
    ) -> None:
        client = client_cls(api_key="test-key")  # type: ignore[call-arg]
        original = _ServerError("connect failed", status_code=503)
        _sdk(client).chat.completions.create = AsyncMock(side_effect=original)

        with pytest.raises(ProviderError) as exc_info:
            async for _ in client.stream(
                messages=[Message(role=Role.USER, content="Hi")], model=model
            ):
                pass
        assert exc_info.value.provider == provider
        assert exc_info.value.__cause__ is original

    async def test_stream_midstream_error_after_chunks(
        self, client_cls: type[BaseLLMClient], provider: str, model: Model
    ) -> None:
        client = client_cls(api_key="test-key")  # type: ignore[call-arg]
        original = _ServerError("stream died", status_code=500)
        # Cerebras type-filters chunks, so its mock must satisfy isinstance.
        spec = ChatChunkResponse if client_cls is CerebrasClient else None
        _sdk(client).chat.completions.create = AsyncMock(
            return_value=_ChunkIterator([_content_chunk("Hel", spec)], error=original)
        )

        received = []
        with pytest.raises(ProviderError) as exc_info:
            async for chunk in client.stream(
                messages=[Message(role=Role.USER, content="Hi")], model=model
            ):
                received.append(chunk)
        assert [c.content for c in received] == ["Hel"]
        assert exc_info.value.provider == provider

    async def test_stream_consumer_break_raises_nothing(
        self,
        client_cls: type[BaseLLMClient],
        provider: str,  # noqa: ARG002 - parametrized alongside the others
        model: Model,
    ) -> None:
        client = client_cls(api_key="test-key")  # type: ignore[call-arg]
        _sdk(client).chat.completions.create = AsyncMock(
            return_value=_ChunkIterator([_content_chunk("a"), _content_chunk("b")])
        )

        async for _ in client.stream(
            messages=[Message(role=Role.USER, content="Hi")], model=model
        ):
            break  # GeneratorExit must not be reported as a provider failure


@pytest.mark.unit
class TestContextWindowClassification:
    async def test_cerebras_400_overflow_becomes_context_window_error(self) -> None:
        client = CerebrasClient(api_key="test-key")
        from cerebras.cloud.sdk import BadRequestError

        original = BadRequestError(
            message="prompt is too long: 200000 tokens",
            body={"error": {"code": "context_length_exceeded"}},
            response=MagicMock(status_code=400),
        )
        _sdk(client).chat.completions.create = AsyncMock(side_effect=original)

        with pytest.raises(ContextWindowExceededError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CEREBRAS_GPT_OSS_120B,
            )
        assert exc_info.value.model == Model.CEREBRAS_GPT_OSS_120B.value
        assert (
            exc_info.value.context_window == Model.CEREBRAS_GPT_OSS_120B.context_window
        )
        assert exc_info.value.__cause__ is original


_TOOLS = [
    ToolDefinition(
        name=ToolName("lookup"),
        description="A tool",
        parameters={"type": "object", "properties": {}},
    )
]


@pytest.mark.unit
class TestOverflowBeforeToolRetry:
    """A 400 that names both an overflow and a tool is an overflow (LL-4):
    classification runs before the tool-retry branch, so the request is
    not resent and the error is not ToolCallGenerationError."""

    _MESSAGE = "prompt is too long: 200000 tokens (tool schemas included)"

    async def test_cerebras(self) -> None:
        from cerebras.cloud.sdk import BadRequestError

        client = CerebrasClient(api_key="test-key")
        original = BadRequestError(
            message=self._MESSAGE,
            body={"error": {"code": "bad_request", "message": self._MESSAGE}},
            response=MagicMock(status_code=400),
        )
        create = AsyncMock(side_effect=original)
        _sdk(client).chat.completions.create = create

        with pytest.raises(ContextWindowExceededError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CEREBRAS_GPT_OSS_120B,
                tools=_TOOLS,
            )
        assert create.call_count == 1

    async def test_openai(self) -> None:
        from openai import BadRequestError

        client = OpenAIClient(api_key="test-key")
        original = BadRequestError(
            message=self._MESSAGE,
            body={
                "error": {"code": "context_length_exceeded", "message": self._MESSAGE}
            },
            response=MagicMock(status_code=400),
        )
        create = AsyncMock(side_effect=original)
        _sdk(client).chat.completions.create = create

        with pytest.raises(ContextWindowExceededError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.GPT_5_NANO,
                tools=_TOOLS,
            )
        assert create.call_count == 1

    async def test_anthropic(self) -> None:
        from anthropic import BadRequestError

        client = AnthropicClient(api_key="test-key")
        original = BadRequestError(
            message=self._MESSAGE,
            body={"message": self._MESSAGE},
            response=MagicMock(status_code=400),
        )
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=original)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        stream = MagicMock(return_value=mock_stream)
        _sdk(client).messages.stream = stream

        with pytest.raises(ContextWindowExceededError):
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CLAUDE_HAIKU_4_5,
                tools=_TOOLS,
            )
        assert stream.call_count == 1


@pytest.mark.unit
class TestAnthropicWrap:
    async def test_complete_wraps_stream_entry_failure(self) -> None:
        client = AnthropicClient(api_key="test-key")
        original = _ServerError("overloaded", status_code=529)
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=original)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        _sdk(client).messages.stream = MagicMock(return_value=mock_stream)

        with pytest.raises(ProviderError) as exc_info:
            await client.complete(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CLAUDE_HAIKU_4_5,
            )
        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.status == 529
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is original

    async def test_stream_wraps_aenter_failure(self) -> None:
        client = AnthropicClient(api_key="test-key")
        original = _ServerError("auth failed", status_code=401)
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=original)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        _sdk(client).messages.stream = MagicMock(return_value=mock_stream)

        with pytest.raises(ProviderError) as exc_info:
            async for _ in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CLAUDE_HAIKU_4_5,
            ):
                pass
        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.status == 401
        assert exc_info.value.retryable is False
        assert exc_info.value.__cause__ is original


@pytest.mark.unit
class TestCerebrasInBandError:
    async def test_error_chunk_raises_provider_error_uncaused(self) -> None:
        """The in-band ErrorChunkResponse passes the NeosianError guard."""
        from cerebras.cloud.sdk.types.chat.chat_completion import ErrorChunkResponse

        client = CerebrasClient(api_key="test-key")
        error_chunk = MagicMock(spec=ErrorChunkResponse)
        error_chunk.error = "mid-stream failure"
        _sdk(client).chat.completions.create = AsyncMock(
            return_value=_ChunkIterator([error_chunk])
        )

        with pytest.raises(ProviderError) as exc_info:
            async for _ in client.stream(
                messages=[Message(role=Role.USER, content="Hi")],
                model=Model.CEREBRAS_GPT_OSS_120B,
            ):
                pass
        assert exc_info.value.provider == "cerebras"
        assert exc_info.value.__cause__ is None
