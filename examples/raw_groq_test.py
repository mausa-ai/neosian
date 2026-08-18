"""Raw Groq API call for latency baseline measurement."""

import asyncio
import os
import time

from groq import AsyncGroq


async def main() -> None:
    api_key = os.environ["GROQ_API_KEY"]

    # Client instantiation outside timer
    client = AsyncGroq(api_key=api_key)

    messages = [
        {"role": "system", "content": "You are a helpful assistant. Be concise."},
        {"role": "user", "content": "testing groqs speed"},
    ]

    # First call (cold)
    start = time.perf_counter()
    response = await client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=messages,
    )
    elapsed1 = time.perf_counter() - start
    print(f"Call 1 (cold): {elapsed1:.2f}s - {response.choices[0].message.content}")

    # Second call (warm connection)
    start = time.perf_counter()
    response = await client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=messages,
    )
    elapsed2 = time.perf_counter() - start
    print(f"Call 2 (warm): {elapsed2:.2f}s - {response.choices[0].message.content}")


if __name__ == "__main__":
    asyncio.run(main())
