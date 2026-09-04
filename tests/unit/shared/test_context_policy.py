"""ContextPolicy: the deliberately-low estimate and the fit check (DESIGN §5)."""

import pytest

from neosian._foundation.llm.base import ImageBlock, Message, Role, TextBlock, ToolCall
from neosian._foundation.shared.context_policy import ContextPolicy
from neosian._foundation.shared.exceptions import ContextWindowExceededError
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ToolCallId,
    ToolName,
)


@pytest.mark.unit
class TestEstimateTokens:
    def test_text_content_by_chars_plus_message_overhead(self) -> None:
        messages = [Message(role=Role.USER, content="x" * 400)]
        assert ContextPolicy().estimate_tokens(messages) == 400 // 4 + 4

    def test_chars_per_token_divides(self) -> None:
        messages = [Message(role=Role.USER, content="x" * 400)]
        assert ContextPolicy(chars_per_token=1).estimate_tokens(messages) == 404

    def test_reasoning_and_tool_calls_counted(self) -> None:
        call = ToolCall(
            id=ToolCallId("c1"), name=ToolName("lookup"), arguments={"q": "ab"}
        )
        messages = [
            Message(role=Role.ASSISTANT, content=None, reasoning="r" * 40),
            Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
        ]
        chars = 40 + len("lookup") + len(str({"q": "ab"}))
        assert ContextPolicy().estimate_tokens(messages) == chars // 4 + 2 * 4

    def test_media_blocks_add_flat_constant(self) -> None:
        messages = [
            Message(
                role=Role.USER,
                content=[TextBlock(text="abcd"), ImageBlock(url="http://x")],
            )
        ]
        assert ContextPolicy().estimate_tokens(messages) == 4 // 4 + 4 + 256


@pytest.mark.unit
class TestEnsureFits:
    def test_passes_under_the_window(self) -> None:
        messages = [Message(role=Role.USER, content="hi")]
        ContextPolicy().ensure_fits(Model.FAKE_SMALL, messages)  # no raise

    def test_raises_on_clear_overflow_with_structure(self) -> None:
        # FAKE_SMALL's window is 8_192 by design (types.py) — keyless testable.
        messages = [Message(role=Role.USER, content="x" * 40_000)]
        with pytest.raises(ContextWindowExceededError) as exc_info:
            ContextPolicy().ensure_fits(Model.FAKE_SMALL, messages)
        error = exc_info.value
        assert error.code == "llm_context_window_exceeded"
        assert error.model == Model.FAKE_SMALL.value
        assert error.context_window == 8_192
        assert error.estimated_tokens is not None
        assert error.estimated_tokens > 8_192
        assert error.provider == "fake"


@pytest.mark.unit
class TestConfigDefault:
    def test_default_on(self) -> None:
        config = AgentConfig(system_prompt="s")
        assert config.context_policy == ContextPolicy()

    def test_none_disables(self) -> None:
        config = AgentConfig(system_prompt="s", context_policy=None)
        assert config.context_policy is None


@pytest.mark.unit
class TestEstimatorHook:
    """`estimator` replaces the character heuristic wholesale (TG-44)."""

    def test_estimator_replaces_the_heuristic(self) -> None:
        seen: list[int] = []

        def count(messages: object) -> int:
            seen.append(len(messages))  # type: ignore[arg-type]
            return 7

        policy = ContextPolicy(estimator=count)
        messages = [Message(role=Role.USER, content="x" * 4000)]
        assert policy.estimate_tokens(messages) == 7
        assert seen == [1]

    def test_ensure_fits_uses_it(self) -> None:
        policy = ContextPolicy(estimator=lambda _m: 10**9)
        with pytest.raises(ContextWindowExceededError) as exc_info:
            policy.ensure_fits(
                Model.FAKE_SMALL, [Message(role=Role.USER, content="hi")]
            )
        assert exc_info.value.estimated_tokens == 10**9
