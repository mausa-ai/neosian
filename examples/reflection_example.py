"""Reflection at close — the memory a session boundary writes (DESIGN §15).

Nothing here asks the agent to remember anything. Facts are stated in
passing; `aclose()` runs the default-on reflection rider, which distills
the session into deliberate memory writes through the same dispatcher
every transport uses — audited under the conversation's id,
dedup-disciplined (it sees the live documents first), spend visible. A
second conversation on the same scope then finds them in its frozen
index without being told.

Usage:
    ANTHROPIC_API_KEY=... python examples/reflection_example.py
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from neosian import (
    AgentConfig,
    Conversation,
    FileStore,
    Model,
    ReflectionConfig,
    format_micro_usd,
)

_ROOT = Path(__file__).resolve().parent.parent / ".neosian" / "tour"
_SCOPE = "user:tour"

configuration = AgentConfig(
    system_prompt="You are a concise assistant. Answer in one or two sentences.",
    model=Model.CLAUDE_SONNET_5,
    enable_todo=False,
)


def _conversation(
    suffix: str, *, reflection: ReflectionConfig | None = None
) -> Conversation:
    conversation_id = datetime.now(UTC).strftime(f"%Y%m%d-%H%M%S-{suffix}")
    return Conversation(
        configuration,
        store=FileStore(_ROOT),
        conversation_id=conversation_id,
        memory_scope=_SCOPE,
        reflection=reflection,
    )


async def main() -> None:
    print("--- session 1: facts in passing, no request to remember")
    first = _conversation("reflect")
    await first.send(
        "I'm Ada, I run the platform team at Lumen. We deploy on Thursdays, "
        "never on the last Friday of a quarter."
    )
    await first.send(
        "Our staging cluster is called harbor. What's a good name for the "
        "new production one?"
    )
    result = await first.aclose()  # the reflection rider
    assert result is not None
    print(f"--- aclose(): reflection via {result.model}, {len(result.writes)} writes")
    for write in result.writes:
        print(f"  {write.command} {write.path} -> v{write.version}")
    if result.usage is not None:
        spend = result.usage.cost_micro_usd(configuration.model) or 0
        print(f"  spend: {format_micro_usd(spend)}")

    print("--- session 2: a fresh conversation on the same scope")
    second = _conversation("recall", reflection=ReflectionConfig(enabled=False))
    async with second:
        response = await second.send(
            "What is our staging cluster called, and when do we deploy?"
        )
        print(f"tools: {[call.name for call in response.tool_calls_made]}")
        print(f"agent: {response.message.content}")


if __name__ == "__main__":
    asyncio.run(main())
