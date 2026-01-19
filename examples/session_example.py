"""Example: Using AgentSession for optimized multi-request scenarios.

AgentSession caches LLM clients across multiple runs, eliminating the
connection overhead (~50-200ms) that occurs when creating new clients.

Usage:
    python examples/session_example.py
"""

import asyncio
import os
from pathlib import Path

from neosian import Agent, AgentConfig, Message, Model, Role


def _load_credentials_from_config() -> None:
    """Load API keys from ~/.neosian/config.toml if not already in environment."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return

    config_path = Path.home() / ".neosian" / "config.toml"
    if not config_path.exists():
        return

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    credentials = config.get("credentials", {})

    if not os.environ.get("GROQ_API_KEY") and (
        groq_key := credentials.get("groq_api_key")
    ):
        os.environ["GROQ_API_KEY"] = groq_key


async def example_stateless() -> None:
    """Example: Stateless mode (current default behavior).

    Each agent.run() creates a fresh HTTP client.
    Use this for one-off calls or serverless functions.
    """
    print("\n--- Stateless Mode ---")
    print("Each run creates a new HTTP client.\n")

    config = AgentConfig(
        system_prompt="You are a helpful assistant. Be concise.",
        tools=[],
        model=Model.GPT_OSS_20B,
        enable_todo=False,
    )
    agent = Agent(config=config)

    # Each call creates a fresh client
    messages = [Message(role=Role.USER, content="Say 'hello'")]
    response = await agent.run(messages, stream=False)
    print(f"Response 1: {response.message.content}")

    messages = [Message(role=Role.USER, content="Say 'world'")]
    response = await agent.run(messages, stream=False)
    print(f"Response 2: {response.message.content}")


async def example_session() -> None:
    """Example: Session mode (optimized for multiple requests).

    All runs within a session share the same HTTP client.
    Use this for web servers, batch processing, or chat loops.
    """
    print("\n--- Session Mode ---")
    print("All runs share a cached HTTP client.\n")

    config = AgentConfig(
        system_prompt="You are a helpful assistant. Be concise.",
        tools=[],
        model=Model.GPT_OSS_20B,
        enable_todo=False,
    )
    agent = Agent(config=config)

    # Session caches clients across runs
    async with agent.session() as session:
        # First run creates and caches the client
        messages = [Message(role=Role.USER, content="Say 'hello'")]
        response = await session.run(messages, stream=False)
        print(f"Response 1: {response.message.content}")

        # Second run reuses the cached client (faster!)
        messages = [Message(role=Role.USER, content="Say 'world'")]
        response = await session.run(messages, stream=False)
        print(f"Response 2: {response.message.content}")

    # Client automatically closed when session exits
    print("\nSession closed, client released.")


async def example_chat_loop() -> None:
    """Example: Chat loop with session (realistic use case).

    Maintains conversation history while reusing the HTTP client.
    """
    print("\n--- Chat Loop with Session ---")
    print("Multi-turn conversation with client reuse.\n")

    config = AgentConfig(
        system_prompt="You are a helpful assistant. Keep track of the conversation.",
        tools=[],
        model=Model.GPT_OSS_20B,
        enable_todo=False,
    )
    agent = Agent(config=config)

    # Maintain conversation history (app's responsibility - neosian is stateless)
    messages: list[Message] = []

    async with agent.session() as session:
        # Turn 1
        messages.append(Message(role=Role.USER, content="My name is Alice."))
        response = await session.run(messages, stream=False)
        messages.append(response.message)
        print("User: My name is Alice.")
        print(f"Assistant: {response.message.content}\n")

        # Turn 2 - model remembers context from messages
        messages.append(Message(role=Role.USER, content="What's my name?"))
        response = await session.run(messages, stream=False)
        messages.append(response.message)
        print("User: What's my name?")
        print(f"Assistant: {response.message.content}\n")

    print(f"Total turns: {len(messages) // 2}")


async def main() -> None:
    """Run all examples."""
    _load_credentials_from_config()

    print("=" * 50)
    print("  AgentSession Examples")
    print("=" * 50)

    await example_stateless()
    await example_session()
    await example_chat_loop()

    print("\n" + "=" * 50)
    print("  Summary")
    print("=" * 50)
    print(
        """
Use agent.run() for:
  - One-off calls
  - Serverless functions (Lambda, Cloud Functions)
  - When you need fresh connections each time

Use agent.session() for:
  - Web servers (FastAPI lifespan)
  - Batch processing multiple items
  - Chat loops / interactive sessions
  - Any scenario with multiple sequential requests
"""
    )


if __name__ == "__main__":
    asyncio.run(main())
