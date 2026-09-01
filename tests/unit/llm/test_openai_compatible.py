"""The generic OpenAI-compatible client: the door's endpoint, key and
dialect (DESIGN §19). OpenAIClient is this client on OpenAI's door; its
own suite (test_openai.py) is the byte-identical regression pin."""

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import omit
from pydantic import BaseModel

from neosian import (
    Model,
    OpenAICompatible,
    ReasoningEffort,
    RegisteredModel,
    register_model,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.openai import (
    OPENAI_DOOR,
    OpenAIClient,
    OpenAICompatibleClient,
)
from neosian._foundation.llm.openai_convert import convert_messages
from neosian._foundation.shared.exceptions import (
    ProviderError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.types import ResponseFormat, ToolCallId, ToolName

XAI = OpenAICompatible(
    name="xai",
    api_key_env="XAI_API_KEY",
    base_url="https://api.x.ai/v1",
    temperature=True,
    reasoning_effort=False,
    reasoning_field="reasoning_content",
    strict_schemas=False,
)
_USER = [Message(role=Role.USER, content="Hi")]


class _Out(BaseModel):
    x: int


def _sdk(client: OpenAICompatibleClient) -> Any:
    return client._client


def _client(door: OpenAICompatible = XAI) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_key="test-key", door=door)


def _grok() -> RegisteredModel:
    return register_model(
        "grok-4",
        provider=XAI,
        context_window=131_072,
        max_output_tokens=16_384,
        supports_reasoning=True,
    )


def _response(**message_fields: object) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Hello"
    response.choices[0].message.tool_calls = None
    for name, value in message_fields.items():
        setattr(response.choices[0].message, name, value)
    response.choices[0].finish_reason = "stop"
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response.usage.prompt_tokens_details = None
    response.model = "grok-4-0709"
    return response


def _chunk(text: str, **delta_fields: object) -> MagicMock:
    chunk = MagicMock()
    chunk.model = "grok-4-0709"
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.choices[0].delta.tool_calls = None
    for name, value in delta_fields.items():
        setattr(chunk.choices[0].delta, name, value)
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    return chunk


async def _aiter(items: list[MagicMock]) -> AsyncIterator[MagicMock]:
    for item in items:
        yield item


def _mock_complete(client: OpenAICompatibleClient, response: MagicMock) -> AsyncMock:
    create = AsyncMock(return_value=response)
    _sdk(client).chat.completions.create = create
    return create


def _mock_stream(client: OpenAICompatibleClient, chunks: list[MagicMock]) -> AsyncMock:
    create = AsyncMock(return_value=_aiter(chunks))
    _sdk(client).chat.completions.create = create
    return create


@pytest.mark.unit
class TestConstruction:
    def test_the_sdk_gets_the_doors_endpoint_and_key(self) -> None:
        client = _client()
        assert str(_sdk(client).base_url).startswith("https://api.x.ai/v1")
        assert _sdk(client).api_key == "test-key"
        assert client._door is XAI

    def test_openai_client_is_this_client_on_openais_door(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = OpenAIClient(api_key="test-key")
        assert isinstance(client, OpenAICompatibleClient)
        assert client._door is OPENAI_DOOR
        assert OPENAI_DOOR.base_url is None  # the SDK's own endpoint
        assert "api.openai.com" in str(_sdk(client).base_url)


@pytest.mark.unit
class TestTemperature:
    async def test_accepted_where_the_door_declares_it(self) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        await client.complete(_USER, model=_grok(), temperature=0.3)
        assert create.call_args.kwargs["temperature"] == 0.3

    async def test_omitted_when_not_given(self) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        await client.complete(_USER, model=_grok())
        assert create.call_args.kwargs["temperature"] is omit

    async def test_streamed_too(self) -> None:
        client = _client()
        create = _mock_stream(client, [_chunk("Hi")])
        async for _ in client.stream(_USER, model=_grok(), temperature=0.3):
            pass
        assert create.call_args.kwargs["temperature"] == 0.3

    async def test_refused_on_a_door_without_it_naming_the_door(self) -> None:
        strict = OpenAICompatible(name="strict", api_key_env="STRICT_KEY")
        with pytest.raises(UnsupportedParameterError, match="'strict'"):
            await _client(strict).complete(
                _USER, model=Model.GPT_5_NANO, temperature=0.3
            )


@pytest.mark.unit
class TestReasoning:
    async def test_effort_dropped_on_a_door_without_the_parameter(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        with caplog.at_level(logging.WARNING):
            await client.complete(
                _USER, model=_grok(), reasoning_effort=ReasoningEffort.HIGH
            )
        assert create.call_args.kwargs["reasoning_effort"] is omit
        assert "dropped" in caplog.text and "'xai'" in caplog.text

    async def test_effort_still_refused_for_a_non_reasoning_model(self) -> None:
        plain = register_model(
            "grok-plain", provider=XAI, context_window=8_192, max_output_tokens=1_024
        )
        with pytest.raises(UnsupportedParameterError):
            await _client().complete(
                _USER, model=plain, reasoning_effort=ReasoningEffort.LOW
            )

    async def test_reasoning_field_lands_on_the_message(self) -> None:
        client = _client()
        _mock_complete(client, _response(reasoning_content="thinking..."))
        response = await client.complete(_USER, model=_grok())
        assert response.message.reasoning == "thinking..."
        assert response.message.content == "Hello"

    async def test_reasoning_field_lands_on_stream_chunks(self) -> None:
        client = _client()
        _mock_stream(client, [_chunk("Hi", reasoning_content="th"), _chunk("!")])
        chunks = [c async for c in client.stream(_USER, model=_grok())]
        assert [c.reasoning for c in chunks] == ["th", None]
        assert [c.content for c in chunks] == ["Hi", "!"]

    async def test_a_door_without_a_reasoning_field_ignores_one(self) -> None:
        client = OpenAIClient(api_key="test-key")
        _mock_complete(client, _response(reasoning_content="leaked?"))
        response = await client.complete(_USER, model=Model.GPT_5_NANO)
        assert response.message.reasoning is None


@pytest.mark.unit
class TestSchemasAndErrors:
    def test_strict_schemas_off_sends_strict_false(self) -> None:
        converted = _client()._convert_response_format(ResponseFormat(schema=_Out))
        assert converted["json_schema"]["strict"] is False  # type: ignore[typeddict-item]

    def test_strict_schemas_on_keeps_the_request(self) -> None:
        converted = OpenAIClient(api_key="k")._convert_response_format(
            ResponseFormat(schema=_Out)
        )
        assert converted["json_schema"]["strict"] is True  # type: ignore[typeddict-item]

    async def test_errors_are_labeled_with_the_door(self) -> None:
        client = _client()
        _sdk(client).chat.completions.create = AsyncMock(
            side_effect=RuntimeError("upstream exploded")
        )
        with pytest.raises(ProviderError) as exc_info:
            await client.complete(_USER, model=_grok())
        assert exc_info.value.provider == "xai"


_SIGNATURE = {"extra_content": {"google": {"thought_signature": "sig-1"}}}
_ASK = [Message(role=Role.USER, content="?")]


def _tool_call_mock(extra: object, *, index: int | None = None) -> MagicMock:
    call = MagicMock()
    call.id = "call_1"
    call.function.name = "oracle"
    call.function.arguments = '{"q": "door"}'
    call.model_extra = extra
    if index is not None:
        call.index = index
    return call


@pytest.mark.unit
class TestToolCallExtras:
    """The provider's own fields on a tool call round-trip opaquely —
    Gemini 3's thought signature (DESIGN §19.3, §19.7)."""

    async def test_complete_keeps_the_providers_fields(self) -> None:
        client = _client()
        response = _response(content=None)
        response.choices[0].message.tool_calls = [_tool_call_mock(_SIGNATURE)]
        _mock_complete(client, response)
        result = await client.complete(_ASK, model=_grok())
        (call,) = result.message.tool_calls
        assert call.extra == _SIGNATURE
        assert call.arguments == {"q": "door"}

    async def test_a_plain_call_carries_none(self) -> None:
        client = _client()
        response = _response(content=None)
        response.choices[0].message.tool_calls = [_tool_call_mock({})]
        _mock_complete(client, response)
        result = await client.complete(_ASK, model=_grok())
        assert result.message.tool_calls[0].extra is None

    async def test_streamed_deltas_accumulate_them(self) -> None:
        client = _client()
        opening = _chunk("", tool_calls=[_tool_call_mock(_SIGNATURE, index=0)])
        closing = _chunk("")
        closing.choices[0].finish_reason = "tool_calls"
        _mock_stream(client, [opening, closing])
        chunks = [chunk async for chunk in client.stream(_ASK, model=_grok())]
        (call,) = [call for chunk in chunks for call in chunk.tool_calls]
        assert call.extra == _SIGNATURE
        assert call.name == "oracle"

    def test_the_wire_echoes_them_back(self) -> None:
        call = ToolCall(
            id=ToolCallId("call_1"),
            name=ToolName("oracle"),
            arguments={"q": "door"},
            extra=_SIGNATURE,
        )
        message = Message(role=Role.ASSISTANT, content=None, tool_calls=[call])
        (wire,) = convert_messages([message])
        sent = cast(dict[str, Any], wire)["tool_calls"][0]
        assert sent["extra_content"] == _SIGNATURE["extra_content"]
        assert sent["function"] == {"name": "oracle", "arguments": '{"q": "door"}'}

        (plain,) = convert_messages(
            [replace(message, tool_calls=[replace(call, extra=None)])]
        )
        assert set(cast(dict[str, Any], plain)["tool_calls"][0]) == {
            "id",
            "type",
            "function",
        }
