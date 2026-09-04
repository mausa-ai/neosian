"""Example: Conversation — the stateful, memory-bearing shell (N2).

The three-line quickstart: construct a store, construct a Conversation,
`send()`. History persists per turn in the home (`~/.neosian`, or
`$NEOSIAN_HOME` — DESIGN §22), resume is constructing again with the
same conversation id, and `memory_scope=project_scope()` gives the
agent durable memory across conversations in this project's scope —
the same scope a Claude Code session's hooks write to from this
directory, so `neosian audit --scope "$(python -c 'import neosian;
print(neosian.project_scope())')"` lists both.

Usage:
    python examples/conversation_example.py
"""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from neosian import AgentConfig, Conversation, FileStore, Model, home, project_scope

_ROOT = home()

configuration = AgentConfig(
    system_prompt=("You are a concise assistant. Answer in one or two sentences."),
    model=Model.CEREBRAS_GPT_OSS_120B,
    enable_todo=False,
)


def _load_credentials_from_config() -> None:
    """Load API keys from ~/.neosian/config.toml if not already in environment."""
    import tomllib

    config_path = Path.home() / ".neosian" / "config.toml"
    if not config_path.exists():
        return

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    credentials = config.get("credentials", {})

    if not os.environ.get("CEREBRAS_API_KEY") and (
        cerebras_key := credentials.get("cerebras_api_key")
    ):
        os.environ["CEREBRAS_API_KEY"] = cerebras_key


async def example_quickstart(conversation_id: str) -> None:
    """The ROADMAP three-line quickstart, plus the aclose sugar."""
    print("\n--- Quickstart ---")

    store = FileStore(_ROOT)
    convo = Conversation(configuration, store=store, conversation_id=conversation_id)
    async with convo:  # aclose() on exit; sends share one client pool
        first = await convo.send("Name one strength of append-only logs.")
        print(f"agent: {first.message.content}")
        # The second send reuses the first send's HTTP client.
        second = await convo.send("And one weakness?")
        print(f"agent: {second.message.content}")


async def example_resume(conversation_id: str) -> None:
    """Resume = constructing again with the same conversation id."""
    print("\n--- Resume ---")

    store = FileStore(_ROOT)  # a fresh store over the same directory
    convo = Conversation(configuration, store=store, conversation_id=conversation_id)
    async with convo:
        await convo.start()
        print(f"replayed {len(convo.messages)} messages from disk")
        response = await convo.send("Summarize what we discussed so far.")
        print(f"agent: {response.message.content}")


async def example_memory_scope(stamp: str) -> None:
    """`memory_scope=` sugar: one mount at `memories`, shared across
    conversations keyed by the scope — record in one, recall in another.
    `project_scope()` spells this directory's scope, where the hooks of a
    foreign agent installed here write too."""
    print("\n--- Memory scope ---")

    store = FileStore(_ROOT)
    scope = project_scope()
    print(f"scope: {scope}")
    recorder = Conversation(
        configuration,
        store=store,
        conversation_id=f"{stamp}-record",
        memory_scope=scope,
    )
    async with recorder:
        await recorder.send("Remember this for later: my favorite color is teal.")

    recaller = Conversation(
        configuration,
        store=store,
        conversation_id=f"{stamp}-recall",
        memory_scope=scope,
    )
    async with recaller:
        response = await recaller.send("What is my favorite color?")
        print(f"agent: {response.message.content}")


async def main() -> None:
    _load_credentials_from_config()
    if not os.environ.get("CEREBRAS_API_KEY"):
        print("CEREBRAS_API_KEY not set — run `neosian configure` first.")
        return

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    conversation_id = f"{stamp}-example"

    await example_quickstart(conversation_id)
    await example_resume(conversation_id)
    await example_memory_scope(stamp)

    turns = _ROOT / "conversations" / conversation_id / "turns.jsonl"
    print(f"\nOn disk: {turns}")


if __name__ == "__main__":
    asyncio.run(main())
