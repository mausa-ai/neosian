"""Agent construction and its per-agent settings: tools, reasoning effort, caching, native memory, the response."""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    Usage,
)
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.types import (
    AgentConfig,
    AnyModel,
    FallbackConfig,
    Model,
    Provider,
    ReasoningEffort,
)
from neosian._foundation.tools.base import Tool, ToolResult
from tests.unit.agent.mocks import create_mock_router

_AGENT_LOGGER = "neosian._foundation.agent.base"


@pytest.mark.unit
class TestAgentInit:
    """Test Agent initialization."""

    def test_agent_init_without_tools(self) -> None:
        """Agent should initialize without tools."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert agent._system_prompt == "You are helpful."
            assert len(agent._tools) == 0

    def test_agent_exposes_its_configuration(self) -> None:
        """Read-only config/max_tool_iterations (DESIGN §3) — the seam
        Conversation derives from."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
                max_tool_iterations=7,
            )
            agent = Agent(config=config)

            assert agent.config is config
            assert agent.max_tool_iterations == 7

    def test_agent_init_with_todo_enabled_by_default(self) -> None:
        """Agent should include todo tool by default."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
            )
            agent = Agent(config=config)

            assert "update_todo" in agent._tools
            assert len(agent._tools) == 1

    def test_agent_init_todo_disabled(self) -> None:
        """Agent should not include todo tool when disabled."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert "update_todo" not in agent._tools
            assert len(agent._tools) == 0

    def test_agent_init_with_tools(self) -> None:
        """Agent should register tools from decorated functions."""

        @Tool(name="greet", description="Greet someone")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[greet],
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert "greet" in agent._tools
            assert len(agent._tool_definitions) == 1

    def test_agent_init_with_tools_and_todo(self) -> None:
        """Agent should register both user tools and todo tool."""

        @Tool(name="greet", description="Greet someone")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[greet],
                enable_todo=True,
            )
            agent = Agent(config=config)

            assert "greet" in agent._tools
            assert "update_todo" in agent._tools
            assert len(agent._tool_definitions) == 2

    def test_agent_rejects_non_tool_functions(self) -> None:
        """Agent should reject functions without @Tool decorator."""

        async def not_a_tool(x: int) -> int:
            return x * 2

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[not_a_tool],  # type: ignore[list-item]
                enable_todo=False,
            )

            with pytest.raises(ValueError, match="not decorated with @Tool"):
                Agent(config=config)

    def test_agent_rejects_user_tool_shadowing_builtin(self) -> None:
        """A user tool named like a builtin raises instead of shadowing it (TG-2)."""

        @Tool(name="update_todo", description="Not the builtin")
        async def update_todo(todos: list[str]) -> ToolResult[str]:
            return ToolResult.ok(str(todos))

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[update_todo],
                enable_todo=True,
            )
            with pytest.raises(ConfigurationError, match="update_todo") as info:
                Agent(config=config)
            assert "builtin.todo" in str(info.value)

    def test_agent_rejects_duplicate_user_tool_names(self) -> None:
        """Two user tools with one name raise; the message names the first (TG-2)."""

        @Tool(name="greet", description="First")
        async def greet_one(name: str) -> ToolResult[str]:
            return ToolResult.ok(name)

        @Tool(name="greet", description="Second")
        async def greet_two(name: str) -> ToolResult[str]:
            return ToolResult.ok(name)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[greet_one, greet_two],
                enable_todo=False,
            )
            with pytest.raises(ConfigurationError, match="greet_one"):
                Agent(config=config)


@pytest.mark.unit
class TestAgentResponse:
    """Test AgentResponse dataclass."""

    def test_agent_response_defaults(self) -> None:
        """AgentResponse should have sensible defaults."""
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Hello"),
        )
        assert response.message.content == "Hello"
        assert response.tool_calls_made == ()
        assert response.tool_results == ()
        assert response.usage.total_tokens == 0
        assert response.usage_by_model == ()
        assert response.turn_messages == ()

    def test_agent_response_is_frozen_with_slots(self) -> None:
        """One immutable value object — rebuilds go through replace()."""
        response = AgentResponse(
            message=Message(role=Role.ASSISTANT, content="Hello"),
        )
        with pytest.raises(AttributeError):
            response.model = "other"  # type: ignore[misc]
        assert not hasattr(response, "__dict__")  # slots


@pytest.mark.unit
class TestAgentReasoningEffort:
    """Test Agent reasoning_effort handling."""

    @pytest.mark.asyncio
    async def test_agent_stores_reasoning_effort(self) -> None:
        """Agent should store reasoning_effort from config."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                model=Model.CEREBRAS_GPT_OSS_120B,
                reasoning_effort=ReasoningEffort.HIGH,
                enable_todo=False,
            )
            agent = Agent(config=config)

            assert agent._reasoning_effort == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_agent_passes_reasoning_effort_to_complete(self) -> None:
        """Agent should pass reasoning_effort to LLM complete() calls."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="gpt-oss-120b",
        )

        mock_router = create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                model=Model.CEREBRAS_GPT_OSS_120B,
                reasoning_effort=ReasoningEffort.HIGH,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Think about this.")]
            await agent.run(messages, stream=False)

            # Verify reasoning_effort was passed to complete()
            mock_client.complete.assert_called_once()
            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_agent_none_reasoning_effort_passed_as_none(self) -> None:
        """Agent should pass None for reasoning_effort when not configured."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="gpt-oss-120b",
        )

        mock_router = create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                model=Model.CEREBRAS_GPT_OSS_120B,
                reasoning_effort=None,  # Explicitly None
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            await agent.run(messages, stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_fallback_silently_drops_reasoning_for_non_supporting_model(
        self,
    ) -> None:
        """Agent should silently drop reasoning_effort when fallback model doesn't support it."""
        # Main model client that fails
        mock_main_client = AsyncMock(spec=BaseLLMClient)
        mock_main_client.complete.side_effect = Exception("Main model failed")

        # Fallback model client that succeeds
        mock_fallback_client = AsyncMock(spec=BaseLLMClient)
        mock_fallback_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Fallback response"),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="llama-3.3-70b-versatile",
        )

        # Router that returns different clients based on provider
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True

        def create_client(model: AnyModel) -> AsyncMock:
            if model.provider == Provider.CEREBRAS:
                # Both are Cerebras, but we need to distinguish by model
                # The first call is for main model, subsequent for fallback
                if mock_router.create_client_for.call_count <= 1:
                    return mock_main_client
                return mock_fallback_client
            return mock_fallback_client

        mock_router.create_client_for.side_effect = create_client

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                model=Model.CEREBRAS_GPT_OSS_120B,  # Supports reasoning
                reasoning_effort=ReasoningEffort.HIGH,
                fallback=FallbackConfig(model=Model.CLAUDE_HAIKU_4_5),  # No reasoning
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Hi")]
            response = await agent.run(messages, stream=False)

            # Response should be from fallback
            assert response.message.content == "Fallback response"

            # Main client should have been called with reasoning_effort
            main_call_kwargs = mock_main_client.complete.call_args.kwargs
            assert main_call_kwargs["reasoning_effort"] == ReasoningEffort.HIGH

            # Fallback client should have been called with None (silently dropped)
            fallback_call_kwargs = mock_fallback_client.complete.call_args.kwargs
            assert fallback_call_kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_main_model_keeps_reasoning_when_supported(self) -> None:
        """Agent should keep reasoning_effort when main model supports it."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content="Reasoned response",
                reasoning="I thought about this...",
            ),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="gpt-oss-120b",
        )

        mock_router = create_mock_router(mock_client)

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                model=Model.CEREBRAS_GPT_OSS_120B,
                reasoning_effort=ReasoningEffort.MEDIUM,
                enable_todo=False,
            )
            agent = Agent(config=config)

            messages = [Message(role=Role.USER, content="Think about this")]
            response = await agent.run(messages, stream=False)

            assert response.message.content == "Reasoned response"
            assert response.message.reasoning == "I thought about this..."

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == ReasoningEffort.MEDIUM


@pytest.mark.unit
class TestAgentCacheConversation:
    """cache_conversation must reach the client call."""

    @pytest.mark.asyncio
    async def test_cache_conversation_false_passed_to_client(self) -> None:
        """AgentConfig(cache_conversation=False) reaches client.complete."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Done."),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You transcribe PDFs.",
                tools=[],
                enable_todo=False,
                cache_conversation=False,
            )
            agent = Agent(config=config)

            await agent.run([Message(role=Role.USER, content="Hi")], stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["cache_conversation"] is False

    @pytest.mark.asyncio
    async def test_cache_conversation_defaults_to_true(self) -> None:
        """Default config keeps conversation caching on."""
        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="Done."),
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
            )
            agent = Agent(config=config)

            await agent.run([Message(role=Role.USER, content="Hi")], stream=False)

            call_kwargs = mock_client.complete.call_args.kwargs
            assert call_kwargs["cache_conversation"] is True


@pytest.mark.unit
class TestAgentNativeMemory:
    """AgentConfig.native_memory marks the registered memory tool (N4)."""

    def _memory(self, tmp_path: Path) -> MemoryConfig:
        return MemoryConfig(
            store=FileStore(tmp_path),
            mounts=(Mount(scope="user:1", mount_path="memories"),),
        )

    def _memory_definition(self, agent: Agent) -> Any:
        [definition] = [d for d in agent._tool_definitions if d.name == "memory"]
        return definition

    def test_flag_marks_the_registered_definition(self, tmp_path: Path) -> None:
        agent = Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.CLAUDE_SONNET_5,
                memory=self._memory(tmp_path),
                native_memory=True,
                enable_todo=False,
            )
        )
        assert self._memory_definition(agent).native_type == "memory_20250818"

    def test_flag_off_leaves_the_definition_unmarked(self, tmp_path: Path) -> None:
        agent = Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.CLAUDE_SONNET_5,
                memory=self._memory(tmp_path),
                enable_todo=False,
            )
        )
        assert self._memory_definition(agent).native_type is None

    def test_flag_without_memory_constructs_cleanly(self) -> None:
        """Ledger #42: the flag never raises — derive_config depends on it."""
        agent = Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.CLAUDE_SONNET_5,
                native_memory=True,
                enable_todo=False,
            )
        )
        assert all(d.name != "memory" for d in agent._tool_definitions)

    def test_warns_on_non_anthropic_model(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_AGENT_LOGGER):
            Agent(
                AgentConfig(
                    system_prompt="test",
                    model=Model.FAKE,
                    memory=self._memory(tmp_path),
                    native_memory=True,
                    enable_todo=False,
                )
            )
        assert any("inert" in r.message for r in caplog.records)

    def test_no_warning_on_anthropic_model(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_AGENT_LOGGER):
            Agent(
                AgentConfig(
                    system_prompt="test",
                    model=Model.CLAUDE_SONNET_5,
                    memory=self._memory(tmp_path),
                    native_memory=True,
                    enable_todo=False,
                )
            )
        assert not [r for r in caplog.records if r.name == _AGENT_LOGGER]
