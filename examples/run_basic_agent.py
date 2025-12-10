"""Example: Running the basic_agent with neosian API.

This script demonstrates how to use an existing agent configuration.

Usage:
    # Set API key from file and run
    GROQ_API_KEY=$(cat ~/Documents/api_keys/groq_api_key.txt) python examples/run_basic_agent.py
"""

import asyncio
import sys
from pathlib import Path

# Add examples directory to path to import basic_agent
sys.path.insert(0, str(Path(__file__).parent))

from basic_agent import configuration  # noqa: E402

from neosian import Agent  # noqa: E402
from neosian._foundation.llm.base import Message, Role  # noqa: E402


async def main() -> None:
    """Run the basic_agent with a simple query."""
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

    print(f"\nTokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")


if __name__ == "__main__":
    asyncio.run(main())
