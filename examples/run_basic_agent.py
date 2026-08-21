"""Example: Running the basic_agent with neosian API.

This script demonstrates how to use an existing agent configuration.

Usage:
    python examples/run_basic_agent.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add examples directory to path to import basic_agent
sys.path.insert(0, str(Path(__file__).parent))

from basic_agent import configuration  # noqa: E402

from neosian import Agent, Message, Role  # noqa: E402


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


async def main() -> None:
    """Run the basic_agent with a simple query."""
    _load_credentials_from_config()
    # Create the agent from the imported configuration
    agent = Agent(config=configuration)

    # Create a conversation with a user message
    messages = [
        Message(role=Role.USER, content="What is the current date and time?"),
    ]

    # Run the agent (non-streaming)
    print("Running basic_agent...")
    response = await agent.run(messages, stream=False)

    # Print the response
    print(f"\nAssistant: {response.message.content}")

    if response.tool_calls_made:
        print(f"\nTool calls made: {len(response.tool_calls_made)}")
        for tc in response.tool_calls_made:
            print(f"  - {tc.name}({tc.arguments})")

    print(
        f"\nTokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out"
    )


if __name__ == "__main__":
    asyncio.run(main())
