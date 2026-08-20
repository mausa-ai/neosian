"""Agent session with persistent LLM clients.

Provides a context manager for reusing LLM clients across multiple agent runs,
reducing connection overhead for high-frequency messaging scenarios.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal, overload

from neosian._foundation.llm.base import BaseLLMClient, Message
from neosian._foundation.shared.types import FallbackState, Provider, ResponseFormat

if TYPE_CHECKING:
    from neosian._foundation.agent.base import Agent
    from neosian._foundation.agent.events import AgentEvent
    from neosian._foundation.agent.response import AgentResponse


class AgentSession:
    """Session with cached LLM clients for reduced latency.

    A session maintains a cache of LLM clients that are reused across multiple
    `run()` calls. This eliminates the connection establishment overhead that
    occurs when creating new clients for each request.

    Sessions also maintain fallback state for "sticky" fallback behavior:
    when a model fails and falls back, subsequent calls continue using the
    fallback model until retry_main_after successful calls.

    Thread-safety: Sessions are safe for concurrent use. The underlying
    SDK clients (AsyncGroq, AsyncOpenAI, AsyncAnthropic) use httpx which
    supports concurrent requests from multiple coroutines.

    Usage patterns:
        - Web servers: Create one session at startup, share across all requests
        - Batch processing: Wrap the entire batch in a single session
        - CLI chat loops: Wrap the conversation loop in a session
        - One-off calls: Use agent.run() directly (no session needed)

    Example:
        async with agent.session() as session:
            # All runs reuse the same underlying HTTP clients
            response1 = await session.run(messages1, stream=False)
            response2 = await session.run(messages2, stream=False)
        # Clients automatically closed here
    """

    def __init__(self, agent: Agent) -> None:
        """Initialize the session.

        Args:
            agent: The parent Agent instance.
        """
        self._agent = agent
        self._clients: dict[Provider, BaseLLMClient] = {}
        self._fallback_state = FallbackState()

    def _get_or_create_client(self, provider: Provider) -> BaseLLMClient:
        """Get a cached client or create and cache a new one.

        Args:
            provider: The provider enum.

        Returns:
            The cached or newly created LLM client.
        """
        if provider not in self._clients:
            self._clients[provider] = self._agent._create_client(provider)
        return self._clients[provider]

    def _rebind(self, agent: Agent) -> None:
        """Point the session at a freshly derived Agent, keeping the client
        cache and sticky fallback state — Conversation rebuilds its agent
        at every compaction boundary (DESIGN §9.5.10) and must not pay a
        reconnect; `derive_config` carries `client_factory` unchanged, so
        the cached clients stay valid."""
        self._agent = agent

    @overload
    async def run(
        self,
        messages: list[Message],
        *,
        stream: Literal[False],
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse: ...

    @overload
    async def run(
        self,
        messages: list[Message],
        *,
        stream: Literal[True],
        response_format: None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def run(
        self,
        messages: list[Message],
        *,
        stream: bool,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse | AsyncIterator[AgentEvent]:
        """Execute the agent with cached clients.

        This method mirrors Agent.run() but uses cached clients from the session
        instead of creating new ones for each call.

        Args:
            messages: Conversation history (without system message).
            stream: If True, yields typed AgentEvent values (DESIGN §6).
                If False, returns AgentResponse.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse when stream=False, AsyncIterator[AgentEvent]
            when stream=True.
        """
        # One funnel with Agent.run — _dispatch validates and builds the
        # RunContext carrying this session's client cache + sticky state.
        return await self._agent._dispatch(
            messages, stream=stream, response_format=response_format, session=self
        )

    async def close(self) -> None:
        """Close all cached clients and release resources.

        This method is called automatically when exiting the session context.
        It ensures all underlying HTTP connections are properly closed.
        """
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    async def __aenter__(self) -> AgentSession:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context manager, closing all clients."""
        await self.close()
