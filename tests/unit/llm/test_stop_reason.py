"""Unit tests for stop-reason normalization and cost estimation."""

import pytest

from neosian._foundation.llm.base import StopReason, Usage, normalize_stop_reason
from neosian._foundation.shared.types import Model


@pytest.mark.unit
class TestNormalizeStopReason:
    """normalize_stop_reason maps provider-native values to StopReason."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Natural completion
            ("end_turn", StopReason.STOP),
            ("stop", StopReason.STOP),
            ("stop_sequence", StopReason.STOP),
            # Truncation
            ("max_tokens", StopReason.MAX_TOKENS),
            ("length", StopReason.MAX_TOKENS),
            # Tool calls
            ("tool_use", StopReason.TOOL_CALLS),
            ("tool_calls", StopReason.TOOL_CALLS),
            ("function_call", StopReason.TOOL_CALLS),
            # Moderation
            ("content_filter", StopReason.CONTENT_FILTER),
            ("refusal", StopReason.CONTENT_FILTER),
            # Unrecognized values fall through to OTHER
            ("pause_turn", StopReason.OTHER),
            ("model_context_window_exceeded", StopReason.OTHER),
            ("something_new", StopReason.OTHER),
        ],
    )
    def test_mapping(self, raw: str, expected: StopReason) -> None:
        assert normalize_stop_reason(raw) is expected

    def test_none_passes_through(self) -> None:
        assert normalize_stop_reason(None) is None


@pytest.mark.unit
class TestUsageCost:
    """Usage.cost estimates USD spend at the model's list prices."""

    def test_cost_with_full_pricing(self) -> None:
        """All four token classes are priced (Anthropic-style pricing)."""
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
        )
        # Opus 5: $5 in, $25 out, $6.25 cache write, $0.50 cache read
        cost = usage.cost(Model.CLAUDE_OPUS_5)
        assert cost == pytest.approx(5.00 + 25.00 + 6.25 + 0.50)

    def test_cost_cache_falls_back_to_input_price(self) -> None:
        """Providers without cache rates price cache tokens at input rate."""
        usage = Usage(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        # Groq gpt-oss-20b has no cache pricing; falls back to $0.10 input
        cost = usage.cost(Model.GROQ_GPT_OSS_20B)
        assert cost == pytest.approx(0.10)

    def test_cost_unpriced_model_returns_none(self) -> None:
        """Models without verified pricing return None, never a guess."""
        usage = Usage(input_tokens=100, output_tokens=100)
        assert Model.CEREBRAS_GEMMA_4_31B.pricing is None
        assert usage.cost(Model.CEREBRAS_GEMMA_4_31B) is None

    def test_zero_usage_costs_zero(self) -> None:
        usage = Usage(input_tokens=0, output_tokens=0)
        assert usage.cost(Model.CLAUDE_SONNET_5) == 0.0
