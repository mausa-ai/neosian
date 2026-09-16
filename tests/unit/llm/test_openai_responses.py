"""The Responses wire (DESIGN §31.5, ledger #250–#252, #255–#257): OpenAI's
door and any `wire="responses"` door, both paths, keyless on the SDK's own
types. Stateless by construction — `store: false`, encrypted reasoning
asked for and replayed from `Message.extra["openai"]`, never a
`previous_response_id`."""

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError, omit
from pydantic import BaseModel

from neosian import (
    Agent,
    AgentConfig,
    DoneEvent,
    Model,
    OpenAICompatible,
    ReasoningEffort,
    Tool,
    ToolResult,
    register_model,
)
from neosian._foundation.llm.base import (
    Message,
    Role,
    StreamChunk,
    TextBlock,
    ToolCall,
    ToolDefinition,
)
from neosian._foundation.llm.openai import (
    OPENAI_DOOR,
    OpenAIClient,
    OpenAICompatibleClient,
)
from neosian._foundation.llm.openai_responses import (
    INCLUDE,
    convert_input,
    reasoning_channel,
)
from neosian._foundation.shared.catalog import XAI
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import (
    ContextWindowExceededError,
    ProviderError,
    ToolCallGenerationError,
    UnsupportedContentError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.models import ModelSpec
from neosian._foundation.shared.types import (
    ResponseFormat,
    ToolCallId,
    ToolChoice,
    ToolName,
)
from tests.unit.llm.sdk_specs import RESPONSES as SPEC, autospec

_USER = [Message(role=Role.USER, content="Hi")]
_TOOL = ToolDefinition(
    name=ToolName("get_time"),
    description="Get the time",
    parameters={"type": "object", "properties": {}},
)
_SECRET = "gAAAAA-encrypted-reasoning"
_ITEM = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [{"type": "summary_text", "text": "thinking"}],
    "encrypted_content": _SECRET,
}
DOOR = OpenAICompatible(
    name="d",
    api_key_env="D_KEY",
    base_url="https://d.example/v1",
    temperature=True,
    wire="responses",
)


class _Out(BaseModel):
    x: int


def _sdk(client: OpenAICompatibleClient) -> Any:
    return client._client


def _door_model(door: OpenAICompatible = DOOR, **overrides: object) -> Any:
    kwargs: dict[str, object] = {
        "provider": door,
        "context_window": 131_072,
        "max_output_tokens": 16_384,
        "supports_reasoning": True,
    }
    kwargs.update(overrides)
    return register_model("door-model", **kwargs)  # type: ignore[arg-type]


def _text(text: str = "Hello") -> Any:
    part = autospec(SPEC["text"])
    part.text = text
    message = autospec(SPEC["message"])
    message.content = [part]
    return message


def _refusal(text: str) -> Any:
    part = autospec(SPEC["refusal"])
    part.refusal = text
    message = autospec(SPEC["message"])
    message.content = [part]
    return message


def _call(
    call_id: str = "call_1", name: str = "get_time", arguments: str = "{}"
) -> Any:
    item = autospec(SPEC["function_call"])
    item.call_id = call_id
    item.name = name
    item.arguments = arguments
    return item


def _reasoning(encrypted: str | None = _SECRET, *summary: str) -> Any:
    item = autospec(SPEC["reasoning"])
    item.id = "rs_1"
    item.encrypted_content = encrypted
    item.summary = [MagicMock(text=text) for text in (summary or ("thinking",))]
    return item


def _response(*output: Any, incomplete: str | None = None) -> Any:
    response = autospec(SPEC["response"])
    response.output = list(output) or [_text()]
    response.model = "gpt-5.6-sol"
    response.incomplete_details = (
        None if incomplete is None else MagicMock(reason=incomplete)
    )
    response.usage.input_tokens = 12
    response.usage.output_tokens = 5
    response.usage.input_tokens_details.cached_tokens = 2
    return response


def _mock_complete(client: OpenAICompatibleClient, response: Any) -> AsyncMock:
    create = AsyncMock(return_value=response)
    _sdk(client).responses.create = create
    return create


def _event(kind: str, **fields: Any) -> Any:
    event = autospec(SPEC[kind])
    event.type = SPEC[kind].type
    for name, value in fields.items():
        setattr(event, name, value)
    return event


async def _events(*events: Any, error: Exception | None = None) -> AsyncIterator[Any]:
    for event in events:
        yield event
    if error is not None:
        raise error


def _mock_stream(
    client: OpenAICompatibleClient, events: AsyncIterator[Any]
) -> AsyncMock:
    create = AsyncMock(return_value=events)
    _sdk(client).responses.create = create
    return create


async def _collect(client: OpenAICompatibleClient, **kwargs: Any) -> list[StreamChunk]:
    return [chunk async for chunk in client.stream(_USER, Model.GPT_5_6_SOL, **kwargs)]


@pytest.mark.unit
class TestTheDoors:
    def test_openai_and_xai_speak_responses(self) -> None:
        assert OPENAI_DOOR.wire == "responses"
        assert XAI.wire == "responses"
        assert XAI.reasoning_field is None  # reasoning rides the items
        assert OpenAICompatible(name="d", api_key_env="D_KEY").wire == "chat"

    def test_the_stopgap_row_fact_is_gone(self) -> None:
        assert not hasattr(ModelSpec, "tools_without_reasoning")


@pytest.mark.unit
class TestTheRequest:
    async def test_the_body_is_stateless(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        await client.complete(_USER, Model.GPT_5_6_SOL)
        kwargs = create.call_args.kwargs
        assert kwargs["store"] is False
        assert kwargs["include"] == list(INCLUDE) == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in kwargs
        assert kwargs["model"] == "gpt-5.6-sol"
        assert kwargs["input"] == [{"role": "user", "content": "Hi"}]
        assert kwargs["max_output_tokens"] == LLMDefaults.MAX_OUTPUT_TOKENS
        for absent in ("tools", "tool_choice", "reasoning", "text", "temperature"):
            assert kwargs.get(absent, omit) is omit, absent
        assert "parallel_tool_calls" not in kwargs

    async def test_tools_are_flat_and_strict_per_door(self) -> None:
        strict = replace(
            _TOOL,
            strict=True,
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        await client.complete(_USER, Model.GPT_5_6_SOL, tools=[_TOOL, strict])
        plain, tight = create.call_args.kwargs["tools"]
        assert plain == {
            "type": "function",
            "name": "get_time",
            "description": "Get the time",
            "parameters": _TOOL.parameters,
            "strict": False,
        }
        assert tight["strict"] is True
        assert tight["parameters"]["required"] == ["q"]
        loose = OpenAICompatibleClient(
            api_key="k", door=replace(DOOR, strict_schemas=False)
        )
        create = _mock_complete(loose, _response())
        await loose.complete(_USER, _door_model(), tools=[strict])
        assert create.call_args.kwargs["tools"][0]["strict"] is False

    @pytest.mark.parametrize(
        ("choice", "expected"),
        [
            (ToolChoice.auto(), "auto"),
            (ToolChoice.required(), "required"),
            (ToolChoice.none(), "none"),
            (ToolChoice.tool("get_time"), {"type": "function", "name": "get_time"}),
        ],
    )
    async def test_tool_choice_in_responses_spelling(
        self, choice: ToolChoice, expected: object
    ) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        await client.complete(
            _USER, Model.GPT_5_6_SOL, tools=[_TOOL], tool_choice=choice
        )
        assert create.call_args.kwargs["tool_choice"] == expected
        assert "parallel_tool_calls" not in create.call_args.kwargs

    async def test_parallel_off_and_a_choice_without_tools(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        serial = ToolChoice.auto(parallel=False)
        await client.complete(
            _USER, Model.GPT_5_6_SOL, tools=[_TOOL], tool_choice=serial
        )
        assert create.call_args.kwargs["parallel_tool_calls"] is False
        await client.complete(
            _USER, Model.GPT_5_6_SOL, tool_choice=ToolChoice.required()
        )
        assert "tool_choice" not in create.call_args.kwargs

    async def test_the_schema_rides_the_text_format(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        await client.complete(
            _USER, Model.GPT_5_6_SOL, response_format=ResponseFormat(schema=_Out)
        )
        fmt = create.call_args.kwargs["text"]["format"]
        assert fmt["type"] == "json_schema" and fmt["name"] == "_Out"
        assert fmt["strict"] is True
        assert fmt["schema"]["additionalProperties"] is False
        loose = OpenAICompatibleClient(
            api_key="k", door=replace(DOOR, strict_schemas=False)
        )
        create = _mock_complete(loose, _response())
        await loose.complete(
            _USER, _door_model(), response_format=ResponseFormat(schema=_Out)
        )
        assert create.call_args.kwargs["text"]["format"]["strict"] is False

    async def test_an_effort_carries_a_summary_and_rides_beside_tools(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        await client.complete(
            _USER,
            Model.GPT_5_6_SOL,
            tools=[_TOOL],
            reasoning_effort=ReasoningEffort.HIGH,
        )
        kwargs = create.call_args.kwargs
        assert kwargs["reasoning"] == {"effort": "high", "summary": "auto"}
        assert kwargs["tools"][0]["name"] == "get_time"  # #249 retired: both at once
        await client.complete(
            _USER, Model.GPT_6_ASTRA, reasoning_effort=ReasoningEffort.MAX
        )
        assert create.call_args.kwargs["reasoning"]["effort"] == "max"

    async def test_max_downgrades_where_the_spec_says(self) -> None:
        client = OpenAICompatibleClient(api_key="k", door=DOOR)
        create = _mock_complete(client, _response())
        await client.complete(
            _USER, _door_model(), reasoning_effort=ReasoningEffort.MAX
        )
        assert create.call_args.kwargs["reasoning"]["effort"] == "high"

    async def test_temperature_per_door(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        with pytest.raises(UnsupportedParameterError):
            await client.complete(_USER, Model.GPT_5_6_SOL, temperature=0.2)
        create.assert_not_called()
        warm = OpenAICompatibleClient(api_key="k", door=DOOR)
        create = _mock_complete(warm, _response())
        await warm.complete(_USER, _door_model(), temperature=0.2)
        assert create.call_args.kwargs["temperature"] == 0.2

    def test_a_history_replays_its_reasoning_items_first(self) -> None:
        history = [
            Message(role=Role.SYSTEM, content="Be brief."),
            Message(role=Role.USER, content="Time?"),
            Message(
                role=Role.ASSISTANT,
                content="Checking.",
                reasoning="thinking",
                tool_calls=[
                    ToolCall(
                        id=ToolCallId("call_1"),
                        name=ToolName("get_time"),
                        arguments={"tz": "UTC"},
                    )
                ],
                extra=reasoning_channel([_ITEM]),
            ),
            Message(role=Role.TOOL, content="noon", tool_call_id=ToolCallId("call_1")),
            Message(role=Role.ASSISTANT, content="It is noon."),
        ]
        assert convert_input(history) == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Time?"},
            _ITEM,
            {"role": "assistant", "content": "Checking."},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_time",
                "arguments": '{"tz": "UTC"}',
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "noon"},
            {"role": "assistant", "content": "It is noon."},
        ]

    def test_another_wires_channel_is_not_replayed(self) -> None:
        anthropic = Message(
            role=Role.ASSISTANT,
            content="x",
            extra={"anthropic": {"thinking_blocks": []}},
        )
        assert convert_input([anthropic]) == [{"role": "assistant", "content": "x"}]

    async def test_block_content_is_refused_not_dropped(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_complete(client, _response())
        with pytest.raises(UnsupportedContentError):
            await client.complete(
                [Message(role=Role.USER, content=[TextBlock(text="hi")])],
                Model.GPT_5_6_SOL,
            )
        create.assert_not_called()


@pytest.mark.unit
class TestTheParse:
    async def test_text_usage_and_model(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_complete(client, _response(_text("Hello")))
        response = await client.complete(_USER, Model.GPT_5_6_SOL)
        assert response.message.content == "Hello"
        assert response.message.tool_calls == []
        assert response.message.extra is None
        assert response.stop_reason == "stop"
        assert response.model == "gpt-5.6-sol"
        assert (response.usage.input_tokens, response.usage.cache_read_tokens) == (
            10,
            2,
        )
        assert response.usage.output_tokens == 5

    async def test_a_call_and_its_reasoning(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_complete(
            client, _response(_reasoning(), _call(arguments='{"tz": "UTC"}'))
        )
        response = await client.complete(_USER, Model.GPT_5_6_SOL, tools=[_TOOL])
        assert response.message.content is None
        assert response.message.tool_calls == [
            ToolCall(
                id=ToolCallId("call_1"),
                name=ToolName("get_time"),
                arguments={"tz": "UTC"},
            )
        ]
        assert response.message.reasoning == "thinking"
        assert response.message.extra == {"openai": {"reasoning_items": [_ITEM]}}
        assert response.stop_reason == "tool_calls"

    async def test_an_item_without_encrypted_content_is_not_replayable(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_complete(client, _response(_reasoning(None, "a", "b"), _text()))
        response = await client.complete(_USER, Model.GPT_5_6_SOL)
        assert response.message.extra is None
        assert response.message.reasoning == "a\n\nb"

    async def test_a_refusal_is_the_content_and_the_stop_reason(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_complete(client, _response(_refusal("No.")))
        response = await client.complete(_USER, Model.GPT_5_6_SOL)
        assert response.message.content == "No."
        assert response.stop_reason == "refusal"

    @pytest.mark.parametrize(
        ("reason", "stop"),
        [("max_output_tokens", "length"), ("content_filter", "content_filter")],
    )
    async def test_incomplete_in_chat_completions_vocabulary(
        self, reason: str, stop: str
    ) -> None:
        client = OpenAIClient(api_key="k")
        _mock_complete(client, _response(_text("Hel"), incomplete=reason))
        response = await client.complete(_USER, Model.GPT_5_6_SOL)
        assert response.stop_reason == stop

    async def test_no_usage_is_zero(self) -> None:
        client = OpenAIClient(api_key="k")
        response = _response()
        response.usage = None
        _mock_complete(client, response)
        assert (await client.complete(_USER, Model.GPT_5_6_SOL)).usage.input_tokens == 0


class _Error(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.unit
class TestRetryAndWrap:
    def _bad_request(self, code: str) -> BadRequestError:
        return BadRequestError(
            message=code,
            body={"error": {"code": code, "message": code}},
            response=MagicMock(status_code=400),
        )

    async def test_a_tool_call_failure_is_retried_then_named(self) -> None:
        client = OpenAIClient(api_key="k")
        create = AsyncMock(side_effect=self._bad_request("invalid_tool_call"))
        _sdk(client).responses.create = create
        with pytest.raises(ToolCallGenerationError):
            await client.complete(_USER, Model.GPT_5_6_SOL, tools=[_TOOL])
        assert create.call_count == LLMDefaults.MAX_TOOL_CALL_RETRIES + 1

    async def test_an_overflow_is_classified_before_the_retry(self) -> None:
        client = OpenAIClient(api_key="k")
        create = AsyncMock(side_effect=self._bad_request("context_length_exceeded"))
        _sdk(client).responses.create = create
        with pytest.raises(ContextWindowExceededError):
            await client.complete(_USER, Model.GPT_5_6_SOL, tools=[_TOOL])
        assert create.call_count == 1

    async def test_a_server_error_wraps_with_the_door_name(self) -> None:
        client = OpenAICompatibleClient(api_key="k", door=DOOR)
        original = _Error("upstream exploded", status_code=500)
        _sdk(client).responses.create = AsyncMock(side_effect=original)
        with pytest.raises(ProviderError) as exc_info:
            await client.complete(_USER, _door_model())
        assert exc_info.value.provider == "d"
        assert exc_info.value.__cause__ is original


@pytest.mark.unit
class TestTheStream:
    async def test_text_then_the_terminal_chunk(self) -> None:
        client = OpenAIClient(api_key="k")
        create = _mock_stream(
            client,
            _events(
                _event("created", response=_response()),
                _event("text_delta", delta="Hel"),
                _event("text_delta", delta="lo"),
                _event("completed", response=_response()),
            ),
        )
        chunks = await _collect(client)
        assert create.call_args.kwargs["stream"] is True
        assert create.call_args.kwargs["store"] is False
        assert [c.content for c in chunks] == ["Hel", "lo", None]
        assert {c.model for c in chunks} == {"gpt-5.6-sol"}
        last = chunks[-1]
        assert last.finish_reason == "stop"
        assert last.usage is not None and last.usage.output_tokens == 5
        assert last.extra is None

    async def test_summary_deltas_are_reasoning(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_stream(
            client,
            _events(
                _event("summary_delta", delta="th"),
                _event("completed", response=_response()),
            ),
        )
        chunks = await _collect(client, reasoning_effort=ReasoningEffort.LOW)
        assert [c.reasoning for c in chunks] == ["th", None]

    async def test_a_call_arrives_as_fragments_and_a_finished_call(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_stream(
            client,
            _events(
                _event("item_added", item=_call(arguments=""), output_index=1),
                _event("arguments_delta", delta='{"tz":', output_index=1),
                _event("arguments_delta", delta=' "UTC"}', output_index=1),
                _event(
                    "item_done", item=_call(arguments='{"tz": "UTC"}'), output_index=1
                ),
                _event("item_done", item=_reasoning(), output_index=0),
                _event("completed", response=_response()),
            ),
        )
        chunks = await _collect(client, tools=[_TOOL])
        fragments = [f for c in chunks for f in c.tool_call_fragments]
        assert [f.fragment for f in fragments] == ['{"tz":', ' "UTC"}']
        assert {(f.id, f.name) for f in fragments} == {("call_1", "get_time")}
        last = chunks[-1]
        assert last.tool_calls == [
            ToolCall(
                id=ToolCallId("call_1"),
                name=ToolName("get_time"),
                arguments={"tz": "UTC"},
            )
        ]
        assert last.finish_reason == "tool_calls"
        assert last.extra == {"openai": {"reasoning_items": [_ITEM]}}

    async def test_a_refusal_and_an_incomplete_response(self) -> None:
        client = OpenAIClient(api_key="k")
        _mock_stream(
            client,
            _events(
                _event("refusal_delta", delta="No."),
                _event("completed", response=_response()),
            ),
        )
        assert [c.finish_reason for c in await _collect(client)] == [None, "refusal"]
        _mock_stream(
            client,
            _events(
                _event("text_delta", delta="Hel"),
                _event(
                    "incomplete", response=_response(incomplete="max_output_tokens")
                ),
            ),
        )
        assert (await _collect(client))[-1].finish_reason == "length"

    async def test_a_failed_response_and_an_error_frame_raise(self) -> None:
        client = OpenAIClient(api_key="k")
        failed = _response()
        failed.error = MagicMock(message="boom")
        _mock_stream(client, _events(_event("failed", response=failed)))
        with pytest.raises(ProviderError, match="boom"):
            await _collect(client)
        _mock_stream(client, _events(_event("error", message="bad frame")))
        with pytest.raises(ProviderError, match="bad frame"):
            await _collect(client)

    async def test_transport_errors_wrap_and_a_break_raises_nothing(self) -> None:
        client = OpenAIClient(api_key="k")
        original = _Error("connect failed", status_code=503)
        _sdk(client).responses.create = AsyncMock(side_effect=original)
        with pytest.raises(ProviderError) as exc_info:
            await _collect(client)
        assert exc_info.value.__cause__ is original
        died = _Error("stream died", status_code=500)
        _mock_stream(client, _events(_event("text_delta", delta="Hel"), error=died))
        received: list[StreamChunk] = []
        with pytest.raises(ProviderError):
            async for chunk in client.stream(_USER, Model.GPT_5_6_SOL):
                received.append(chunk)
        assert [c.content for c in received] == ["Hel"]
        _mock_stream(
            client,
            _events(_event("text_delta", delta="a"), _event("text_delta", delta="b")),
        )
        async for _ in client.stream(_USER, Model.GPT_5_6_SOL):
            break


@Tool(name="get_time", description="Get the time")
async def _get_time() -> ToolResult[str]:
    return ToolResult.ok("noon")


@pytest.mark.unit
class TestTheLoop:
    """The done-when (§31.5): an agent on the default OpenAI row reasons
    inside its tool loop with nothing stored at the provider — the second
    call replays the first's encrypted reasoning item."""

    def _agent(self, client: OpenAICompatibleClient) -> Agent:
        return Agent(
            AgentConfig(
                system_prompt="You are a test agent.",
                model=Model.GPT_5_6_SOL,
                tools=[_get_time],
                enable_todo=False,
                reasoning_effort=ReasoningEffort.HIGH,
                client_factory=lambda _m: client,
            )
        )

    async def test_blocking(self) -> None:
        client = OpenAIClient(api_key="k")
        create = AsyncMock(
            side_effect=[
                _response(_reasoning(), _call()),
                _response(_text("It is noon.")),
            ]
        )
        _sdk(client).responses.create = create
        response = await self._agent(client).run(
            [Message(role=Role.USER, content="Time?")], stream=False
        )
        assert response.message.content == "It is noon."
        first, second = (call.kwargs for call in create.call_args_list)
        assert first["store"] is second["store"] is False
        assert first["reasoning"] == {"effort": "high", "summary": "auto"}
        assert second["input"][-3:] == [
            _ITEM,
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_time",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": '{"success": true, "data": "noon"}',
            },
        ]

    async def test_streaming(self) -> None:
        client = OpenAIClient(api_key="k")
        create = AsyncMock(
            side_effect=[
                _events(
                    _event("item_done", item=_reasoning(), output_index=0),
                    _event("item_added", item=_call(arguments=""), output_index=1),
                    _event("arguments_delta", delta="{}", output_index=1),
                    _event("item_done", item=_call(), output_index=1),
                    _event("completed", response=_response()),
                ),
                _events(
                    _event("text_delta", delta="It is noon."),
                    _event("completed", response=_response()),
                ),
            ]
        )
        _sdk(client).responses.create = create
        events = [
            e
            async for e in await self._agent(client).run(
                [Message(role=Role.USER, content="Time?")], stream=True
            )
        ]
        assert any(isinstance(e, DoneEvent) for e in events)
        second = create.call_args_list[1].kwargs
        assert second["input"][-3]["encrypted_content"] == _SECRET
        assert second["input"][-2]["type"] == "function_call"
