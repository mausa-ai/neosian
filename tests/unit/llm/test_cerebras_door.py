"""The Cerebras rows on the OpenAI door (DESIGN §19.3, ledger #218): the
two knobs the move earned, the two hardenings, and the usage `or 0`."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError, omit

from neosian import Model, OpenAICompatible, ReasoningEffort
from neosian._foundation.llm.base import Message, Role, ToolDefinition, Usage
from neosian._foundation.llm.openai import OpenAICompatibleClient
from neosian._foundation.llm.openai_convert import is_tool_call_error
from neosian._foundation.shared.catalog import CEREBRAS
from neosian._foundation.shared.exceptions import ConfigurationError, ProviderError
from neosian._foundation.shared.types import ToolName

_ASK = [Message(role=Role.USER, content="Hi")]
_MODEL = Model.CEREBRAS_GPT_OSS_120B
_TOOLS = [
    ToolDefinition(
        name=ToolName("lookup"),
        description="A tool",
        parameters={"type": "object", "properties": {}},
    )
]
_TOOL_FAILED = {"code": "tool_use_failed", "message": "Failed"}


def _sdk(client: OpenAICompatibleClient) -> Any:
    return client._client


def _client(door: OpenAICompatible = CEREBRAS) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(api_key="test-key", door=door)


def _bad_request(body: object) -> BadRequestError:
    return BadRequestError(message="400", body=body, response=MagicMock())


def _response(
    *, prompt_tokens: object = 10, completion_tokens: object = 5, **fields: object
) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "Hello"
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning = None
    for name, value in fields.items():
        setattr(response.choices[0].message, name, value)
    response.choices[0].finish_reason = "stop"
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    response.usage.prompt_tokens_details = None
    response.model = "gpt-oss-120b"
    return response


def _chunk(text: str | None = "", **delta_fields: object) -> MagicMock:
    chunk = MagicMock()
    chunk.model = "gpt-oss-120b"
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].delta.reasoning = None
    for name, value in delta_fields.items():
        setattr(chunk.choices[0].delta, name, value)
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    return chunk


def _partial_call(arguments: str) -> MagicMock:
    call = MagicMock()
    call.index = 0
    call.id = "call_1"
    call.function.name = "lookup"
    call.function.arguments = arguments
    call.model_extra = None
    return call


async def _aiter(items: list[MagicMock]) -> AsyncIterator[MagicMock]:
    for item in items:
        yield item


def _mock_complete(client: OpenAICompatibleClient, *responses: object) -> AsyncMock:
    create = AsyncMock(side_effect=list(responses))
    _sdk(client).chat.completions.create = create
    return create


def _mock_stream(client: OpenAICompatibleClient, chunks: list[MagicMock]) -> AsyncMock:
    create = AsyncMock(return_value=_aiter(chunks))
    _sdk(client).chat.completions.create = create
    return create


async def _collect(client: OpenAICompatibleClient, **kwargs: Any) -> list[Any]:
    return [chunk async for chunk in client.stream(_ASK, model=_MODEL, **kwargs)]


@pytest.mark.unit
class TestTheDoor:
    def test_the_shipped_door(self) -> None:
        assert CEREBRAS.name == "cerebras"
        assert CEREBRAS.api_key_env == "CEREBRAS_API_KEY"
        assert CEREBRAS.base_url == "https://api.cerebras.ai/v1"
        assert CEREBRAS.temperature and CEREBRAS.reasoning_effort
        assert CEREBRAS.reasoning_field == "reasoning"
        assert CEREBRAS.reasoning_format == "parsed"
        assert CEREBRAS.retry_temperature == 0.3
        assert Model.CEREBRAS_GPT_OSS_120B.door is CEREBRAS
        assert Model.CEREBRAS_QWEN_3_8_27B.door is CEREBRAS

    def test_the_sdk_gets_the_doors_endpoint(self) -> None:
        client = _client()
        assert str(_sdk(client).base_url).startswith("https://api.cerebras.ai/v1")

    @pytest.mark.parametrize(
        "override",
        [
            {"reasoning_format": "parsed", "reasoning_effort": False},
            {"retry_temperature": 0.3},
            {"retry_temperature": 2.5, "temperature": True},
            {"retry_temperature": -0.1, "temperature": True},
        ],
    )
    def test_a_knob_needs_its_parameter(self, override: dict[str, Any]) -> None:
        with pytest.raises(ConfigurationError, match="door 'd'"):
            OpenAICompatible(name="d", api_key_env="D_KEY", **override)


@pytest.mark.unit
class TestToolCallErrorShapes:
    """Both body shapes on one wire: OpenAI nests under `error`, Cerebras
    answers flat (#218)."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"error": _TOOL_FAILED}, True),
            (_TOOL_FAILED, True),
            ({"code": "bad_request", "message": "Invalid tool call"}, True),
            ({"error": {"code": "invalid_tool_call", "message": ""}}, True),
            ({"error": {"code": "x", "message": "Invalid function arguments"}}, True),
            ({"error": {"code": "invalid_request", "message": "Bad request"}}, False),
            ({"code": "invalid_request", "message": "Bad request"}, False),
            (None, False),
            ({"error": "string error not dict"}, False),
        ],
    )
    def test_the_shapes(self, body: object, expected: bool) -> None:
        assert is_tool_call_error(body) is expected
        assert _client()._is_tool_call_error(_bad_request(body)) is expected


@pytest.mark.unit
class TestRetryTemperature:
    async def test_a_temperature_in_play_retries_cooler(self) -> None:
        client = _client()
        create = _mock_complete(client, _bad_request(_TOOL_FAILED), _response())
        result = await client.complete(
            _ASK, model=_MODEL, tools=_TOOLS, temperature=0.9
        )
        assert result.message.content == "Hello"
        first, second = create.call_args_list
        assert first.kwargs["temperature"] == 0.9
        assert second.kwargs["temperature"] == 0.3

    async def test_no_temperature_stays_omitted_across_the_retry(self) -> None:
        """LL-15: the retry never injects a temperature the caller omitted."""
        client = _client()
        create = _mock_complete(client, _bad_request(_TOOL_FAILED), _response())
        await client.complete(_ASK, model=_MODEL, tools=_TOOLS)
        assert all(c.kwargs["temperature"] is omit for c in create.call_args_list)

    async def test_a_door_without_the_knob_retries_as_sent(self) -> None:
        door = OpenAICompatible(name="plain", api_key_env="PLAIN_KEY", temperature=True)
        client = _client(door)
        create = _mock_complete(client, _bad_request(_TOOL_FAILED), _response())
        await client.complete(_ASK, model=_MODEL, tools=_TOOLS, temperature=0.9)
        assert [c.kwargs["temperature"] for c in create.call_args_list] == [0.9, 0.9]


@pytest.mark.unit
class TestReasoningFormat:
    """`reasoning_format` rides `extra_body` beside a sent effort — never
    alone: the endpoint rejects null reasoning fields."""

    async def test_complete_sends_it_with_the_effort(self) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        await client.complete(_ASK, model=_MODEL, reasoning_effort=ReasoningEffort.LOW)
        assert create.call_args.kwargs["reasoning_effort"] == "low"
        assert create.call_args.kwargs["extra_body"] == {"reasoning_format": "parsed"}

    async def test_stream_sends_it_with_the_effort(self) -> None:
        client = _client()
        create = _mock_stream(client, [_chunk("Hi")])
        await _collect(client, reasoning_effort=ReasoningEffort.HIGH)
        assert create.call_args.kwargs["reasoning_effort"] == "high"
        assert create.call_args.kwargs["extra_body"] == {"reasoning_format": "parsed"}

    async def test_max_downgrades_to_high(self) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        await client.complete(_ASK, model=_MODEL, reasoning_effort=ReasoningEffort.MAX)
        assert create.call_args.kwargs["reasoning_effort"] == "high"

    async def test_absent_without_an_effort(self) -> None:
        client = _client()
        create = _mock_complete(client, _response())
        await client.complete(_ASK, model=_MODEL)
        assert create.call_args.kwargs["reasoning_effort"] is omit
        assert create.call_args.kwargs["extra_body"] is None

    async def test_absent_on_a_door_without_the_knob(self) -> None:
        door = OpenAICompatible(name="plain", api_key_env="PLAIN_KEY")
        client = _client(door)
        create = _mock_complete(client, _response())
        await client.complete(_ASK, model=_MODEL, reasoning_effort=ReasoningEffort.LOW)
        assert create.call_args.kwargs["extra_body"] is None


@pytest.mark.unit
class TestReasoningContent:
    async def test_complete_reads_the_reasoning_field(self) -> None:
        client = _client()
        _mock_complete(client, _response(reasoning="thinking..."))
        result = await client.complete(_ASK, model=_MODEL)
        assert result.message.reasoning == "thinking..."
        assert result.model == "gpt-oss-120b"

    async def test_stream_reads_the_reasoning_field(self) -> None:
        client = _client()
        _mock_stream(client, [_chunk(None, reasoning="hmm"), _chunk("Hi")])
        chunks = await _collect(client)
        assert [c.reasoning for c in chunks] == ["hmm", None]
        assert chunks[1].content == "Hi"


@pytest.mark.unit
class TestUsage:
    async def test_cached_tokens_split_the_input(self) -> None:
        client = _client()
        response = _response(prompt_tokens=1000, completion_tokens=50)
        response.usage.prompt_tokens_details = MagicMock(cached_tokens=600)
        _mock_complete(client, response)
        result = await client.complete(_ASK, model=_MODEL)
        assert result.usage == Usage(
            input_tokens=400, output_tokens=50, cache_read_tokens=600
        )

    async def test_null_counts_read_as_zero(self) -> None:
        client = _client()
        _mock_complete(client, _response(prompt_tokens=None, completion_tokens=None))
        result = await client.complete(_ASK, model=_MODEL)
        assert result.usage == Usage(input_tokens=0, output_tokens=0)

    async def test_no_usage_block_reads_as_zero(self) -> None:
        client = _client()
        response = _response()
        response.usage = None
        _mock_complete(client, response)
        result = await client.complete(_ASK, model=_MODEL)
        assert result.usage == Usage(input_tokens=0, output_tokens=0)

    async def test_stream_null_counts_read_as_zero(self) -> None:
        client = _client()
        chunk = _chunk("Hi")
        chunk.usage = MagicMock(
            prompt_tokens=None, completion_tokens=None, prompt_tokens_details=None
        )
        _mock_stream(client, [chunk])
        (received,) = await _collect(client)
        assert received.usage == Usage(input_tokens=0, output_tokens=0)


@pytest.mark.unit
class TestStreamedToolCallFinish:
    """The flush rule of the OpenAI wire (LL-13, LL-7), now one wire."""

    async def test_usage_on_a_content_chunk_is_read(self) -> None:
        client = _client()
        chunk = _chunk("Hi")
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
        )
        create = _mock_stream(client, [chunk])
        (received,) = await _collect(client)
        assert received.content == "Hi"
        assert received.usage == Usage(input_tokens=10, output_tokens=5)
        assert create.call_args.kwargs["temperature"] is omit

    async def test_a_stop_finish_releases_the_call(self) -> None:
        client = _client()
        closing = _chunk("")
        closing.choices[0].finish_reason = "stop"
        _mock_stream(
            client, [_chunk("", tool_calls=[_partial_call('{"a": 1}')]), closing]
        )
        chunks = await _collect(client)
        assert chunks[-1].tool_calls[0].arguments == {"a": 1}
        assert chunks[-1].finish_reason == "stop"

    async def test_a_truncated_tool_call_names_the_stop_reason(self) -> None:
        client = _client()
        closing = _chunk("")
        closing.choices[0].finish_reason = "length"
        _mock_stream(client, [_chunk("", tool_calls=[_partial_call('{"a":')]), closing])
        with pytest.raises(ProviderError, match="stop reason: length") as info:
            await _collect(client)
        assert info.value.provider == "cerebras"
