"""Tests for AgentSession."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.events import ContentEvent
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    Usage,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    AnyModel,
    FallbackConfig,
    Model,
    Provider,
    SystemPrompt,
)


def _create_mock_client() -> AsyncMock:
    """Create a mock LLM client."""
    mock_client = AsyncMock(spec=BaseLLMClient)
    mock_client.complete.return_value = CompletionResponse(
        message=Message(role=Role.ASSISTANT, content="Hello!"),
        usage=Usage(input_tokens=10, output_tokens=5),
        model="test-model",
    )
    mock_client.close = AsyncMock()
    return mock_client


def _create_mock_router(mock_client: BaseLLMClient | None = None) -> MagicMock:
    """Create a mock ProviderRouter."""
    if mock_client is None:
        mock_client = _create_mock_client()

    mock_router = MagicMock()
    mock_router.create_client_for.return_value = mock_client
    mock_router.has_provider.return_value = True
    return mock_router


@pytest.mark.unit
class TestAgentSessionInit:
    """Test AgentSession initialization."""

    def test_session_init_empty_cache(self) -> None:
        """Session should start with empty client cache."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            assert len(session._clients) == 0

    def test_session_stores_agent_reference(self) -> None:
        """Session should store reference to parent agent."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=_create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            assert session._agent is agent


@pytest.mark.unit
class TestAgentSessionClientCaching:
    """Test AgentSession client caching behavior."""

    def test_get_or_create_creates_on_first_call(self) -> None:
        """Session should create client on first call for a provider."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            client = session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)

            assert client is mock_client
            assert Provider.CEREBRAS in session._clients
            mock_router.create_client_for.assert_called_once_with(
                Model.CEREBRAS_GPT_OSS_120B
            )

    def test_get_or_create_reuses_on_second_call(self) -> None:
        """Session should reuse cached client on subsequent calls."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            # First call creates
            client1 = session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)
            # Second call reuses
            client2 = session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)

            assert client1 is client2
            # Router should only be called once
            mock_router.create_client_for.assert_called_once()

    def test_different_providers_cached_separately(self) -> None:
        """Session should cache different providers separately."""
        mock_client_cerebras = _create_mock_client()
        mock_client_openai = _create_mock_client()

        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client_for.side_effect = [
            mock_client_cerebras,
            mock_client_openai,
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            cerebras_client = session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)
            openai_client = session._get_or_create_client(Model.GPT_5_NANO)

            assert cerebras_client is mock_client_cerebras
            assert openai_client is mock_client_openai
            assert len(session._clients) == 2


@pytest.mark.unit
class TestAgentSessionClose:
    """Test AgentSession close behavior."""

    @pytest.mark.asyncio
    async def test_close_closes_all_cached_clients(self) -> None:
        """Session.close() should close all cached clients."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            # Create a cached client
            session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)

            # Close session
            await session.close()

            mock_client.close.assert_called_once()
            assert len(session._clients) == 0

    @pytest.mark.asyncio
    async def test_close_clears_cache(self) -> None:
        """Session.close() should clear the client cache."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)
            session = AgentSession(agent)

            session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)
            assert len(session._clients) == 1

            await session.close()

            assert len(session._clients) == 0


@pytest.mark.unit
class TestAgentSessionContextManager:
    """Test AgentSession async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_returns_session(self) -> None:
        """Context manager should yield the session instance."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                assert isinstance(session, AgentSession)
                assert session._agent is agent

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exit(self) -> None:
        """Context manager should close session on exit."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                # Create a cached client
                session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)

            # After exit, client should be closed
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exception(self) -> None:
        """Context manager should close session even on exception."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            with pytest.raises(RuntimeError):
                async with agent.session() as session:
                    session._get_or_create_client(Model.CEREBRAS_GPT_OSS_120B)
                    raise RuntimeError("Test error")

            # Client should still be closed
            mock_client.close.assert_called_once()


@pytest.mark.unit
class TestAgentSessionRun:
    """Test AgentSession.run execution."""

    @pytest.mark.asyncio
    async def test_session_run_uses_cached_client(self) -> None:
        """Session.run() should use cached clients."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                messages = [Message(role=Role.USER, content="Hi")]

                # First run
                response1 = await session.run(messages, stream=False)
                # Second run
                response2 = await session.run(messages, stream=False)

                assert response1.message.content == "Hello!"
                assert response2.message.content == "Hello!"

                # Router should only create client once
                mock_router.create_client_for.assert_called_once()
                # Client.complete should be called twice
                assert mock_client.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_session_run_returns_agent_response(self) -> None:
        """Session.run(stream=False) should return AgentResponse."""
        mock_client = _create_mock_client()
        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                messages = [Message(role=Role.USER, content="Hi")]
                response = await session.run(messages, stream=False)

                assert response.message.role == Role.ASSISTANT
                assert response.message.content == "Hello!"
                assert response.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_session_run_streaming_returns_iterator(self) -> None:
        """Session.run(stream=True) should return AsyncIterator."""
        mock_client = _create_mock_client()

        async def mock_stream(
            *args: object, **kwargs: object  # noqa: ARG001
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content="Hello ")
            yield StreamChunk(content="world!")
            yield StreamChunk(finish_reason="stop")

        mock_client.stream = mock_stream

        mock_router = _create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                messages = [Message(role=Role.USER, content="Hi")]
                result = await session.run(messages, stream=True)

                # Result should be an async iterator
                assert hasattr(result, "__anext__")

                events = [event async for event in result]

                # ready, two content deltas, done
                assert len(events) == 4
                assert isinstance(events[1], ContentEvent)
                assert events[1].content == "Hello "


@pytest.mark.unit
class TestAgentSessionMultipleClients:
    """Test AgentSession with multiple providers."""

    @pytest.mark.asyncio
    async def test_fallback_caches_both_providers(self) -> None:
        """Session should cache clients for both primary and fallback providers."""
        mock_client_cerebras = _create_mock_client()
        mock_client_openai = _create_mock_client()

        # Cerebras client fails, OpenAI succeeds
        mock_client_cerebras.complete.side_effect = Exception("Cerebras unavailable")
        mock_client_openai.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello from OpenAI!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="gpt-5-nano",
        )

        mock_router = MagicMock()
        mock_router.has_provider.return_value = True

        def create_client_side_effect(model: AnyModel) -> AsyncMock:
            if model.provider == Provider.CEREBRAS:
                return mock_client_cerebras
            return mock_client_openai

        mock_router.create_client_for.side_effect = create_client_side_effect

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt=SystemPrompt("You are helpful."),
                model=Model.CEREBRAS_GPT_OSS_120B,  # Cerebras model
                fallback=FallbackConfig(model=Model.GPT_5_NANO),  # OpenAI fallback
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            async with agent.session() as session:
                messages = [Message(role=Role.USER, content="Hi")]
                response = await session.run(messages, stream=False)

                # Should get response from OpenAI after Cerebras fails
                assert response.message.content == "Hello from OpenAI!"

                # Both clients should be cached
                assert len(session._clients) == 2
                assert Provider.CEREBRAS in session._clients
                assert Provider.OPENAI in session._clients
