"""Compaction with recall_turn — paging, not deletion (DESIGN §9.6).

A Conversation renders a *view* of its append-only history: recent
turns verbatim, aged turns as one-line log entries carrying a turn-ref,
and the built-in `recall_turn` tool re-hydrates any entry on demand.
Nothing stored ever changes. Here a long fact is stated, the boundary
runs explicitly (`compact()` — the manual idiom; the automatic trigger
rides `ModelSpec.context_window`), the log line it produced is printed,
and a later question makes the model page the original turn back in.
Spend is visible: the digest call's µ$ rides the result.

Usage:
    ANTHROPIC_API_KEY=... python examples/compaction_example.py
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from neosian import (
    AgentConfig,
    CompactionConfig,
    Conversation,
    FileStore,
    Model,
    format_micro_usd,
)

_ROOT = Path(__file__).resolve().parent.parent / ".neosian" / "tour"

configuration = AgentConfig(
    system_prompt="You are a concise assistant. Answer in one sentence.",
    model=Model.CLAUDE_SONNET_5,
    enable_todo=False,
)

_FACT = (
    "For the record: the staging database credentials rotate every Tuesday "
    "at 03:00 UTC, the rotation is documented in runbook RB-4471, and the "
    "on-call engineer must confirm the new secret in Vault before 04:00."
)


async def main() -> None:
    conversation_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-compaction")
    convo = Conversation(
        configuration,
        store=FileStore(_ROOT),
        conversation_id=conversation_id,
        # Age everything but the newest turn, and keep log lines short
        # enough that the fact's tail lives only in the store.
        compaction=CompactionConfig(hot_turns=1, digest_chars=24),
    )
    async with convo:
        await convo.send(_FACT)
        await convo.send("Unrelated: name one prime number below ten.")

        result = await convo.compact()
        print(f"--- compact(): {len(result.entries)} entries via {result.model}")
        for entry in result.entries:
            print(f"  turn {entry.turn} ({entry.kind}): {entry.text}")
        if result.usage is not None:
            spend = result.usage.cost_micro_usd(configuration.model) or 0
            print(f"  spend: {format_micro_usd(spend)}")

        print("--- a question only the paged turn can answer")
        response = await convo.send(
            "Which runbook documents the staging credential rotation?"
        )
        print(f"tools: {[call.name for call in response.tool_calls_made]}")
        print(f"agent: {response.message.content}")


if __name__ == "__main__":
    asyncio.run(main())
