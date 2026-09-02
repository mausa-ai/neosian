"""Pacing for the candidate lanes: a door's rate limit, respected on our side.

A rate limit is an account property, not a dialect, so it never becomes an
`OpenAICompatible` knob (DESIGN §19.3): the external tier paces the door's
client instead. One `Pacer` per door for the whole process — every test
in a lane shares it (a clock per test let consecutive probes burst past
the tier: run 33556146015's kimi lane) — spaces request starts
`60 / requests_per_minute` seconds apart, plus a tenth for the
provider's window boundary, across every client built on it (the agent
creates a client per attempt); a 429 that still arrives — a rate limit
or an overloaded engine — waits a growing multiple of the interval and
retries, up to six attempts, on a stream only before its first chunk
(run 33595428001's kimi lane lost two cells to overloads that outlasted
three) — the SDK's own backoff runs underneath. Injected through the
`client_factory` seam the harness honors for scriptless cells, so every
model call a cell makes — turns, reflection, maintenance — is paced.
"""

import asyncio
import time
from collections.abc import AsyncIterator

from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    StreamChunk,
    ToolDefinition,
)
from neosian._foundation.llm.openai import OpenAICompatibleClient
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import ProviderError
from neosian._foundation.shared.types import AnyModel, ReasoningEffort, ResponseFormat
from tests.external.candidates import Candidate

_ATTEMPTS = 6
_MARGIN = 1.1  # a tenth over the tier: the provider's minute is not ours
_PACERS: dict[str, "Pacer"] = {}


class Pacer:
    """The shared clock: one per door, however many clients or tests ride
    it. Slots are reserved synchronously (no lock, so no event-loop
    binding — pytest gives every test its own loop), then awaited."""

    def __init__(self, requests_per_minute: int) -> None:
        self.interval = 60.0 / requests_per_minute * _MARGIN
        self._next_at = 0.0

    @classmethod
    def of(cls, candidate: Candidate) -> "Pacer | None":
        rpm = candidate.requests_per_minute
        if rpm is None:
            return None
        return _PACERS.setdefault(candidate.name, cls(rpm))

    async def slot(self) -> None:
        now = time.monotonic()
        start = max(now, self._next_at)
        self._next_at = start + self.interval
        if start > now:
            await asyncio.sleep(start - now)


class PacedClient(BaseLLMClient):
    """A client whose requests start on the pacer's slots."""

    def __init__(self, inner: BaseLLMClient, pacer: Pacer) -> None:
        self._inner = inner
        self._pacer = pacer

    async def complete(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        server_compaction: bool = False,
    ) -> CompletionResponse:
        attempt = 0
        while True:
            await self._pacer.slot()
            try:
                return await self._inner.complete(
                    messages,
                    model,
                    tools=tools,
                    temperature=temperature,
                    response_format=response_format,
                    reasoning_effort=reasoning_effort,
                    max_tokens=max_tokens,
                    cache_conversation=cache_conversation,
                    server_compaction=server_compaction,
                )
            except ProviderError as exc:
                attempt += 1
                if exc.status != 429 or attempt == _ATTEMPTS:
                    raise
                await asyncio.sleep(self._pacer.interval * attempt)

    async def stream(
        self,
        messages: list[Message],
        model: AnyModel,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
        cache_conversation: bool = True,
        server_compaction: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        attempt = 0
        while True:
            await self._pacer.slot()
            started = False
            try:
                async for chunk in self._inner.stream(
                    messages,
                    model,
                    tools=tools,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    max_tokens=max_tokens,
                    cache_conversation=cache_conversation,
                    server_compaction=server_compaction,
                ):
                    started = True
                    yield chunk
                return
            except ProviderError as exc:
                attempt += 1
                if started or exc.status != 429 or attempt == _ATTEMPTS:
                    raise
                await asyncio.sleep(self._pacer.interval * attempt)

    async def close(self) -> None:
        await self._inner.close()


def door_client(
    candidate: Candidate, api_key: str, pacer: Pacer | None
) -> BaseLLMClient:
    """The candidate's client, paced when its account states a limit."""
    client = OpenAICompatibleClient(api_key=api_key, door=candidate.door)
    return client if pacer is None else PacedClient(client, pacer)
