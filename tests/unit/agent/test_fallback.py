"""Fallback gates: capability-aware routing and server compaction across a switch."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.blocking import execute_agent_core
from neosian._foundation.agent.context import Attempt, RunContext
from neosian._foundation.agent.fallback import (
    ensure_fallback_viable,
    unsupported_content_types,
)
from neosian._foundation.agent.tool_scope import ToolScope
from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompactionBlock,
    CompletionResponse,
    Message,
    ModelUsage,
    Role,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import UnsupportedContentError
from neosian._foundation.shared.types import (
    AgentConfig,
    FallbackConfig,
    Model,
    Provider,
)
from tests.unit.agent.mocks import create_mock_router


@pytest.mark.unit
class TestCapabilityAwareFallback:
    """Media-bearing conversations never downgrade to a non-supporting model."""

    def _doc_messages(self) -> list[Message]:
        from neosian._foundation.llm.base import DocumentBlock, TextBlock

        return [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data="JVBERi0="),
                    TextBlock(text="Transcribe this."),
                ],
            ),
        ]

    def test_unsupported_content_fallback_skip_keeps_ledger(self) -> None:
        """The content gate re-raises the caller's error with the billed
        ledger attached, as the window gate already does (AG-5)."""
        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(),
        ):
            agent = Agent(
                config=AgentConfig(
                    system_prompt="S",
                    model=Model.CLAUDE_SONNET_5,
                    fallback=FallbackConfig(model=Model.CEREBRAS_QWEN_3_8_27B),
                    enable_todo=False,
                )
            )
        ctx = agent._run_context(None, ToolScope())
        messages = [Message(role=Role.SYSTEM, content="S"), *self._doc_messages()]
        attempt = Attempt.start(Model.CLAUDE_SONNET_5, messages, ctx.ledger)
        attempt.record("claude", Usage(input_tokens=10, output_tokens=5))

        with pytest.raises(UnsupportedContentError) as info:
            ensure_fallback_viable(
                agent, UnsupportedContentError("no documents"), messages, attempt
            )
        assert info.value.usage == Usage(input_tokens=10, output_tokens=5)
        assert info.value.usage_by_model == (
            ModelUsage(model="claude", usage=Usage(input_tokens=10, output_tokens=5)),
        )

    @pytest.mark.asyncio
    async def test_fallback_skipped_when_model_lacks_support(self) -> None:
        """Transient main failure + doc message + Cerebras fallback -> no fallback try."""
        from neosian._foundation.shared.exceptions import ModelFailedError

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.side_effect = RuntimeError("rate limited")
        fallback_client = AsyncMock(spec=BaseLLMClient)

        clients = {
            Provider.ANTHROPIC: main_client,
            Provider.CEREBRAS: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client_for.side_effect = lambda model: clients[
            model.provider
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You transcribe PDFs.",
                tools=[],
                enable_todo=False,
                model=Model.CLAUDE_SONNET_5,
                fallback=FallbackConfig(model=Model.CEREBRAS_QWEN_3_8_27B),
            )
            agent = Agent(config=config)

            with pytest.raises(ModelFailedError):
                await agent.run(self._doc_messages(), stream=False)

            fallback_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_content_error_bypasses_wrapping_without_fallback(
        self,
    ) -> None:
        """No fallback configured: UnsupportedContentError re-raises unwrapped."""
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        mock_client = AsyncMock(spec=BaseLLMClient)
        mock_client.complete.side_effect = UnsupportedContentError(
            "Provider 'openai' does not support multimodal content blocks."
        )

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=create_mock_router(mock_client),
        ):
            config = AgentConfig(
                system_prompt="You are helpful.",
                tools=[],
                enable_todo=False,
                model=Model.GPT_5_6_LUNA,
            )
            agent = Agent(config=config)

            with pytest.raises(UnsupportedContentError):
                await agent.run(self._doc_messages(), stream=False)

    @pytest.mark.asyncio
    async def test_fallback_to_supporting_model_still_works(self) -> None:
        """Main can't handle the doc, but a Claude fallback picks it up."""
        from neosian._foundation.shared.exceptions import UnsupportedContentError

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.side_effect = UnsupportedContentError(
            "Provider 'openai' does not support multimodal content blocks."
        )
        fallback_client = AsyncMock(spec=BaseLLMClient)
        fallback_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="# Transcription"),
            usage=Usage(input_tokens=100, output_tokens=50),
            model="claude-sonnet-5",
            stop_reason="end_turn",
        )

        clients = {
            Provider.OPENAI: main_client,
            Provider.ANTHROPIC: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client_for.side_effect = lambda model: clients[
            model.provider
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You transcribe PDFs.",
                tools=[],
                enable_todo=False,
                model=Model.GPT_5_6_LUNA,
                fallback=FallbackConfig(model=Model.CLAUDE_SONNET_5),
            )
            agent = Agent(config=config)

            response = await agent.run(self._doc_messages(), stream=False)

            assert response.message.content == "# Transcription"
            assert response.stop_reason == "end_turn"
            fallback_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sticky_fallback_routes_media_to_main(self) -> None:
        """Sticky-on-Cerebras session + doc message -> straight to the Claude main."""
        from neosian._foundation.shared.types import FallbackState

        main_client = AsyncMock(spec=BaseLLMClient)
        main_client.complete.return_value = CompletionResponse(
            message=Message(role=Role.ASSISTANT, content="# Transcription"),
            usage=Usage(input_tokens=100, output_tokens=50),
            model="claude-sonnet-5",
        )
        fallback_client = AsyncMock(spec=BaseLLMClient)

        clients = {
            Provider.ANTHROPIC: main_client,
            Provider.CEREBRAS: fallback_client,
        }
        mock_router = MagicMock()
        mock_router.has_provider.return_value = True
        mock_router.create_client_for.side_effect = lambda model: clients[
            model.provider
        ]

        with patch(
            "neosian._foundation.agent.base.ProviderRouter",
            return_value=mock_router,
        ):
            config = AgentConfig(
                system_prompt="You transcribe PDFs.",
                tools=[],
                enable_todo=False,
                model=Model.CLAUDE_SONNET_5,
                fallback=FallbackConfig(model=Model.CEREBRAS_QWEN_3_8_27B),
            )
            agent = Agent(config=config)

            fallback_state = FallbackState(
                using_fallback=True, successful_fallback_calls=1
            )
            ctx = RunContext(
                agent=agent,
                acquire=agent._create_client,
                hooks=agent._hooks,
                fallback_state=fallback_state,
            )
            response = await execute_agent_core(ctx, self._doc_messages())

            assert response.message.content == "# Transcription"
            fallback_client.complete.assert_not_called()
            # Main handled it - sticky state resets
            assert fallback_state.using_fallback is False


@pytest.mark.unit
class TestServerCompactionThreading:
    """AgentConfig.server_compaction reaches every client call (N4)."""

    @pytest.mark.asyncio
    async def test_flag_reaches_the_client(self) -> None:
        fake = FakeClient(FakeScript(turns=(FakeTurn(content="ok"),)))
        agent = Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.FAKE,
                enable_todo=False,
                client_factory=lambda _: fake,
                server_compaction=True,
            )
        )
        await agent.run([Message(role=Role.USER, content="hi")], stream=False)
        assert fake.calls[-1].server_compaction is True

    @pytest.mark.asyncio
    async def test_flag_defaults_off(self) -> None:
        fake = FakeClient(FakeScript(turns=(FakeTurn(content="ok"),)))
        agent = Agent(
            AgentConfig(
                system_prompt="test",
                model=Model.FAKE,
                enable_todo=False,
                client_factory=lambda _: fake,
            )
        )
        await agent.run([Message(role=Role.USER, content="hi")], stream=False)
        assert fake.calls[-1].server_compaction is False


@pytest.mark.unit
class TestCompactionFallbackGate:
    """A compaction-bearing history cannot move off the support set."""

    def _bearing(self) -> list[Message]:
        return [
            Message(role=Role.USER, content="hi"),
            Message(
                role=Role.ASSISTANT,
                content=[CompactionBlock(content="summary")],
            ),
        ]

    def test_unsupported_target_reports_compaction(self) -> None:
        assert unsupported_content_types(Model.FAKE, self._bearing()) == ["compaction"]
        assert unsupported_content_types(Model.CLAUDE_HAIKU_4_5, self._bearing()) == [
            "compaction"
        ]

    def test_supported_target_reports_nothing(self) -> None:
        assert unsupported_content_types(Model.CLAUDE_SONNET_5, self._bearing()) == []

    def test_plain_history_is_unaffected(self) -> None:
        plain = [Message(role=Role.USER, content="hi")]
        assert unsupported_content_types(Model.FAKE, plain) == []
