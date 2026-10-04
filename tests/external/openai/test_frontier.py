"""Release qualification for current GPT tiers and their effort settings."""

import pytest

from neosian import Message, Model, ReasoningEffort, Role
from neosian._foundation.llm.openai import OpenAIClient


@pytest.mark.parametrize(
    "model",
    [Model.GPT_6_1_SOL, Model.GPT_6_ASTRA, Model.GPT_6_LUNA],
    ids=lambda m: m.value,
)
@pytest.mark.parametrize("effort", list(ReasoningEffort))
async def test_current_efforts_and_stateless_replay(
    openai_api_key: str,
    model: Model,
    effort: ReasoningEffort,
) -> None:
    if effort is ReasoningEffort.NONE and not model.supports_no_effort:
        pytest.skip("This model does not accept explicit none")
    client = OpenAIClient(api_key=openai_api_key)
    messages = [Message(role=Role.USER, content="What is 19 times 23? Answer briefly.")]
    try:
        first = await client.complete(
            messages, model, reasoning_effort=effort, max_tokens=8192
        )
        assert first.message.content and first.usage.output_tokens > 0
        messages.extend(
            [first.message, Message(role=Role.USER, content="Now add one.")]
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
