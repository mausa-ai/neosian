"""Raw Groq API call for latency baseline measurement."""

import asyncio
import time

from groq import AsyncGroq


async def main() -> None:
    # Load API key from file
    with open("~/Documents/api_keys/groq_api_key.txt") as f:
        api_key = f.read().strip()

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
