"""Benchmark: Session vs Stateless performance comparison.

This script demonstrates the latency difference between:
1. Stateless mode: Fresh HTTP client created for each request
2. Session mode: HTTP client reused across requests

Usage:
    python examples/session_benchmark.py

    Or with explicit API key:
    GROQ_API_KEY=gsk_xxx python examples/session_benchmark.py
"""

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

from neosian import Agent, AgentConfig, Message, Model, Role


def _load_credentials_from_config() -> None:
    """Load API keys from ~/.neosian/config.toml if not already in environment."""
    import tomllib

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

    if not os.environ.get("OPENAI_API_KEY") and (
        openai_key := credentials.get("openai_api_key")
    ):
        os.environ["OPENAI_API_KEY"] = openai_key

    if not os.environ.get("ANTHROPIC_API_KEY") and (
        anthropic_key := credentials.get("anthropic_api_key")
    ):
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""

    name: str
    num_requests: int
    total_time_ms: float
    times_ms: list[float]

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.num_requests

    @property
    def first_request_ms(self) -> float:
        return self.times_ms[0] if self.times_ms else 0.0

    @property
    def subsequent_avg_ms(self) -> float:
        if len(self.times_ms) <= 1:
            return 0.0
        return sum(self.times_ms[1:]) / (len(self.times_ms) - 1)


async def benchmark_stateless(agent: Agent, num_requests: int) -> BenchmarkResult:
    """Benchmark stateless mode (fresh client each request)."""
    times: list[float] = []

    start_total = time.perf_counter()
    for i in range(num_requests):
        messages = [
            Message(role=Role.USER, content=f"Say 'hello {i}' and nothing else.")
        ]

        start = time.perf_counter()
        await agent.run(messages, stream=False)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    total_time = (time.perf_counter() - start_total) * 1000

    return BenchmarkResult(
        name="Stateless",
        num_requests=num_requests,
        total_time_ms=total_time,
        times_ms=times,
    )


async def benchmark_session(agent: Agent, num_requests: int) -> BenchmarkResult:
    """Benchmark session mode (client reused across requests)."""
    times: list[float] = []

    start_total = time.perf_counter()
    async with agent.session() as session:
        for i in range(num_requests):
            messages = [
                Message(role=Role.USER, content=f"Say 'hello {i}' and nothing else.")
            ]

            start = time.perf_counter()
            await session.run(messages, stream=False)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)

    total_time = (time.perf_counter() - start_total) * 1000

    return BenchmarkResult(
        name="Session",
        num_requests=num_requests,
        total_time_ms=total_time,
        times_ms=times,
    )


def print_result(result: BenchmarkResult) -> None:
    """Print benchmark result in a formatted way."""
    print(f"\n{'=' * 50}")
    print(f"  {result.name} Mode")
    print(f"{'=' * 50}")
    print(f"  Requests:            {result.num_requests}")
    print(f"  Total time:          {result.total_time_ms:.0f} ms")
    print(f"  Average per request: {result.avg_time_ms:.0f} ms")
    print(f"  First request:       {result.first_request_ms:.0f} ms")
    print(f"  Subsequent avg:      {result.subsequent_avg_ms:.0f} ms")
    print()
    print("  Per-request times:")
    for i, t in enumerate(result.times_ms):
        marker = " (client init)" if i == 0 else ""
        print(f"    Request {i + 1}: {t:.0f} ms{marker}")


def print_comparison(stateless: BenchmarkResult, session: BenchmarkResult) -> None:
    """Print comparison between stateless and session modes."""
    total_saved = stateless.total_time_ms - session.total_time_ms
    total_pct = (total_saved / stateless.total_time_ms) * 100

    subsequent_saved = stateless.subsequent_avg_ms - session.subsequent_avg_ms
    subsequent_pct = (
        (subsequent_saved / stateless.subsequent_avg_ms) * 100
        if stateless.subsequent_avg_ms > 0
        else 0
    )

    print(f"\n{'=' * 50}")
    print("  COMPARISON")
    print(f"{'=' * 50}")
    print(f"  Total time saved:      {total_saved:.0f} ms ({total_pct:.1f}% faster)")
    print(
        f"  Subsequent req saved:  {subsequent_saved:.0f} ms ({subsequent_pct:.1f}% faster)"
    )
    print()
    print("  Why Session is faster:")
    print("    - HTTP client created once, reused for all requests")
    print("    - TCP connection + TLS handshake done once")
    print("    - Connection pooling benefits from keep-alive")
    print()
    print("  Recommendation:")
    print("    - Use session for web servers (FastAPI lifespan)")
    print("    - Use session for batch processing")
    print("    - Use session for chat loops")
    print("    - Use stateless for one-off calls")


async def main() -> None:
    """Run the benchmark."""
    # Load credentials from config file if not in environment
    _load_credentials_from_config()

    print("\nNeosian Session vs Stateless Benchmark")
    print("=" * 50)

    # Create a minimal agent (no tools, no guardrails for clean timing)
    config = AgentConfig(
        system_prompt="You are a helpful assistant. Be extremely concise.",
        tools=[],
        model=Model.GROQ_GPT_OSS_20B,
        enable_todo=False,
    )
    agent = Agent(config=config)

    num_requests = 5
    print(f"\nRunning {num_requests} requests in each mode...")
    print("(Using Groq API with minimal prompts)\n")

    # Warm up - make one request to ensure API is responsive
    print("Warming up API...")
    warmup_messages = [Message(role=Role.USER, content="Say 'ready'")]
    await agent.run(warmup_messages, stream=False)
    print("Warm-up complete.\n")

    # Run benchmarks
    print("Running stateless benchmark...")
    stateless_result = await benchmark_stateless(agent, num_requests)

    print("Running session benchmark...")
    session_result = await benchmark_session(agent, num_requests)

    # Print results
    print_result(stateless_result)
    print_result(session_result)
    print_comparison(stateless_result, session_result)


if __name__ == "__main__":
    asyncio.run(main())
