"""A row that calls tools only without reasoning (ledger #249): on Chat
Completions the GPT-5.6 family answers a tool call at reasoning_effort
"none" and 400s at any other effort, the parameter's absence included."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from openai import omit

from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.types import Model, ReasoningEffort, ToolName
from tests.unit.llm.sdk_specs import OPENAI as SPEC, autospec

_MESSAGES = [Message(role=Role.USER, content="What time is it?")]
_TOOLS = [
    ToolDefinition(
        name=ToolName("get_time"),
        description="Get the time",
        parameters={"type": "object", "properties": {}},
    )
]


def _client() -> tuple[OpenAIClient, AsyncMock]:
    response = autospec(SPEC["completion"])
    response.choices = [autospec(SPEC["choice"])]
    response.choices[0].message.content = "noon"
    response.choices[0].message.tool_calls = None
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response.model = "gpt-5.6-sol"
    client = OpenAIClient(api_key="test-key")
    create = AsyncMock(return_value=response)
    sdk: Any = client._client
    sdk.chat.completions.create = create
    return client, create


@pytest.mark.unit
class TestToolsWithoutReasoning:
    def test_the_gpt_5_6_rows_carry_the_fact(self) -> None:
        rows = {m for m in Model if m.spec.tools_without_reasoning}
        assert rows == {Model.GPT_5_6_SOL, Model.GPT_5_6_TERRA, Model.GPT_5_6_LUNA}

    async def test_tools_send_none_on_complete(self) -> None:
        client, create = _client()
        await client.complete(_MESSAGES, Model.GPT_5_6_SOL, tools=_TOOLS)
        assert create.call_args.kwargs["reasoning_effort"] == "none"

    async def test_tools_send_none_on_stream(self) -> None:
        async def no_chunks() -> AsyncIterator[object]:
            return
            yield

        client, create = _client()
        create.return_value = no_chunks()
        async for _ in client.stream(_MESSAGES, Model.GPT_5_6_TERRA, tools=_TOOLS):
            pass
        assert create.call_args.kwargs["reasoning_effort"] == "none"

    async def test_an_asked_effort_with_tools_is_refused_by_name(self) -> None:
        client, create = _client()
        with pytest.raises(UnsupportedParameterError, match="gpt-5.6-luna"):
            await client.complete(
                _MESSAGES,
                Model.GPT_5_6_LUNA,
                tools=_TOOLS,
                reasoning_effort=ReasoningEffort.LOW,
            )
        create.assert_not_called()

    async def test_without_tools_the_effort_is_untouched(self) -> None:
        client, create = _client()
        await client.complete(
            _MESSAGES, Model.GPT_5_6_SOL, reasoning_effort=ReasoningEffort.HIGH
        )
        assert create.call_args.kwargs["reasoning_effort"] == "high"
        await client.complete(_MESSAGES, Model.GPT_5_6_SOL)
        assert create.call_args.kwargs["reasoning_effort"] is omit

    async def test_gpt_5_1_keeps_reasoning_with_tools(self) -> None:
        client, create = _client()
        await client.complete(
            _MESSAGES, Model.GPT_5_1, tools=_TOOLS, reasoning_effort=ReasoningEffort.LOW
        )
        assert create.call_args.kwargs["reasoning_effort"] == "low"
