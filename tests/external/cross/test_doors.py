"""The candidate doors' dialects, probed live (DESIGN §19.3, §19.7).

One parametrized file in cross/, like the baselines and the catalog
smoke: every probe self-skips on the candidate's key. A red probe is a
finding about the door — the knob it earns, or the limit §19.7 records —
never a broken test; the classes the provider docs predict are listed
there (a json_object-only endpoint on structured output, a `stop` finish
on a streamed tool turn, a reasoning field under another name).
"""

from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

from neosian import RegisteredModel
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolDefinition,
    text_of,
)
from neosian._foundation.shared.schema import validate_json
from neosian._foundation.shared.types import ReasoningEffort, ResponseFormat, ToolName
from tests.external.candidates import CANDIDATES, Candidate, register
from tests.external.pacing import Pacer, door_client

type Door = tuple[RegisteredModel, BaseLLMClient]

_ORACLE = ToolDefinition(
    name=ToolName("oracle"),
    description="Returns the oracle's number for a question.",
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)
_ASK = [
    Message(
        role=Role.SYSTEM,
        content="Answer only by calling the oracle tool, then report its number.",
    ),
    Message(role=Role.USER, content="What number does the oracle give for 'door'?"),
]
_THINK = [
    Message(
        role=Role.USER,
        content="Is 91 prime? Think it through, then answer yes or no.",
    )
]


class _Capital(BaseModel):
    city: str
    country: str


@pytest.fixture(params=CANDIDATES, ids=lambda c: c.name)
async def door(request: pytest.FixtureRequest) -> AsyncIterator[Door]:
    candidate: Candidate = request.param
    key = request.getfixturevalue(candidate.key_fixture)  # skips when unset
    client = door_client(candidate, key, Pacer.of(candidate))
    try:
        yield register(candidate), client
    finally:
        await client.close()


class TestDoors:
    async def test_system_prompt_is_honored(self, door: Door) -> None:
        model, client = door
        response = await client.complete(
            messages=[
                Message(role=Role.SYSTEM, content="You only respond with 'PONG'."),
                Message(role=Role.USER, content="PING"),
            ],
            model=model,
        )
        assert "PONG" in text_of(response.message).upper()
        assert response.usage.input_tokens > 0

    async def test_tool_call_round_trip(self, door: Door) -> None:
        """Call, result, answer — the shape every memory turn takes."""
        model, client = door
        first = await client.complete(messages=_ASK, model=model, tools=[_ORACLE])
        assert first.message.tool_calls, first.message
        call = first.message.tool_calls[0]
        assert call.name == "oracle"
        history = [
            *_ASK,
            first.message,
            Message(role=Role.TOOL, content="4271", tool_call_id=call.id),
        ]
        final = await client.complete(messages=history, model=model, tools=[_ORACLE])
        assert "4271" in text_of(final.message)

    async def test_streaming_content_and_finish(self, door: Door) -> None:
        model, client = door
        chunks = [
            chunk
            async for chunk in client.stream(
                messages=[Message(role=Role.USER, content="Count from 1 to 5.")],
                model=model,
            )
        ]
        assert any(chunk.content for chunk in chunks)
        assert any(chunk.finish_reason for chunk in chunks)

    async def test_streamed_tool_call(self, door: Door) -> None:
        """The streaming loop's detection path: a door that finishes a
        tool turn with `stop` drops the call (openai.py) — red here."""
        model, client = door
        chunks = [
            chunk
            async for chunk in client.stream(
                messages=_ASK, model=model, tools=[_ORACLE]
            )
        ]
        calls = [call for chunk in chunks for call in chunk.tool_calls]
        finishes = [chunk.finish_reason for chunk in chunks if chunk.finish_reason]
        assert calls, f"no streamed tool call; finish_reason={finishes}"
        assert calls[0].name == "oracle"

    async def test_structured_output(self, door: Door) -> None:
        model, client = door
        response = await client.complete(
            messages=[
                Message(role=Role.USER, content="The capital of France, as JSON.")
            ],
            model=model,
            response_format=ResponseFormat(schema=_Capital),
        )
        answer = validate_json(_Capital, text_of(response.message))
        assert answer.city.lower() == "paris"

    async def test_reasoning_arrives_on_a_path(self, door: Door) -> None:
        """A wrong `reasoning_field` name reads as `None` by contract
        (§19.3), so a reasoning model on a door with a field must show
        reasoning on one path or the other — or the name is wrong."""
        model, client = door
        if model.door.reasoning_field is None or not model.supports_reasoning:
            pytest.skip(f"the {model.door.name} door exposes no reasoning to read")
        response = await client.complete(
            messages=_THINK, model=model, reasoning_effort=ReasoningEffort.HIGH
        )
        chunks = [
            chunk
            async for chunk in client.stream(
                messages=_THINK, model=model, reasoning_effort=ReasoningEffort.HIGH
            )
        ]
        assert response.message.reasoning or any(chunk.reasoning for chunk in chunks)

    async def test_temperature_is_accepted(self, door: Door) -> None:
        model, client = door
        if not model.door.temperature:
            pytest.skip(f"the {model.door.name} door refuses temperature by design")
        response = await client.complete(
            messages=[Message(role=Role.USER, content="Say 'hello' and nothing else.")],
            model=model,
            temperature=0.2,
        )
        assert text_of(response.message)
