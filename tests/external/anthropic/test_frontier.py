"""Release qualification for current Claude: effort and bound-thinking replay."""

import pytest

from neosian import Message, Model, ReasoningEffort, Role
from neosian._foundation.llm.anthropic import AnthropicClient

MODELS = (
    Model.CLAUDE_SONNET_5_5,
    Model.CLAUDE_OPUS_5_5,
    Model.CLAUDE_FABLE_5_1,
    Model.CLAUDE_HAIKU_5_5,
)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.value)
@pytest.mark.parametrize(
    "effort", [e for e in ReasoningEffort if e is not ReasoningEffort.NONE]
)
async def test_effort_and_continuation(
    anthropic_api_key: str,
    model: Model,
    effort: ReasoningEffort,
) -> None:
    client = AnthropicClient(api_key=anthropic_api_key)
    messages = [
        Message(role=Role.SYSTEM, content="Answer the arithmetic question briefly."),
        Message(role=Role.USER, content="What is 19 times 23?"),
    ]
    try:
        first = await client.complete(
            messages, model, reasoning_effort=effort, max_tokens=8192
        )
        assert first.message.content and first.usage.output_tokens > 0
        # This is how a refreshed memory prefix changes between Conversation turns.
        messages[0] = Message(
            role=Role.SYSTEM, content="Answer briefly. New memory: use English."
        )
        messages.extend(
            [
                first.message,
                Message(role=Role.USER, content="Now add one to that answer."),
            ]
        )
        chunks = [
            chunk
            async for chunk in client.stream(
                messages, model, reasoning_effort=effort, max_tokens=8192
            )
        ]
        assert any(chunk.content for chunk in chunks)
        assert chunks[-1].usage is not None
    finally:
        await client.close()


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.value)
async def test_signed_thinking_survives_a_changed_memory_prefix(
    anthropic_api_key: str,
    model: Model,
) -> None:
    client = AnthropicClient(api_key=anthropic_api_key)
    messages = [
        Message(
            role=Role.SYSTEM, content="Solve the problem and give a concise answer."
        ),
        Message(
            role=Role.USER,
            content=(
                "Find the smallest positive integer n with remainders 17, 29, 41, 53, "
                "67 modulo 97, 101, 103, 107, 109 respectively. Verify every remainder."
            ),
        ),
    ]
    try:
        first = await client.complete(
            messages, model, reasoning_effort=ReasoningEffort.HIGH, max_tokens=8192
        )
        assert (first.message.extra or {}).get(
            "anthropic"
        ), "No signed thinking to replay"
        messages[0] = Message(
            role=Role.SYSTEM, content="New memory: use English. Give concise answers."
        )
        messages.extend(
            [
                first.message,
                Message(role=Role.USER, content="What was the remainder modulo 109?"),
            ]
        )
        second = await client.complete(messages, model, max_tokens=4096)
        assert second.message.content
    finally:
        await client.close()
