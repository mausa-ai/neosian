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
class TestUsageCostMicroUsd:
    """Usage.cost_micro_usd prices usage in integer micro-USD."""

    def test_cost_with_full_pricing(self) -> None:
        """All four token classes are priced (Anthropic-style pricing)."""
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        # Opus 5: $5 in, $25 out, $0.50 cache read, $6.25 cache write
        cost = usage.cost_micro_usd(Model.CLAUDE_OPUS_5)
        assert cost == 5_000_000 + 25_000_000 + 500_000 + 6_250_000

    def test_cost_cache_falls_back_to_input_rate(self) -> None:
        """Providers without cache rates price cache tokens at input rate."""
        usage = Usage(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        # Cerebras gpt-oss-120b has no cache pricing; falls back to 250_000 µ$ input
        assert usage.cost_micro_usd(Model.CEREBRAS_GPT_OSS_120B) == 250_000

    def test_cost_rounds_up_never_down(self) -> None:
        """Ceiling division: any fractional micro-USD bills a whole one."""
        # 1 input token on Cerebras 20B = 100_000 / 1_000_000 = 0.1 µ$ → 1 µ$
        usage = Usage(input_tokens=1, output_tokens=0)
        assert usage.cost_micro_usd(Model.CEREBRAS_GPT_OSS_120B) == 1

    def test_cost_unpriced_model_returns_none(self) -> None:
        """Models without verified pricing return None, never 0."""
        usage = Usage(input_tokens=100, output_tokens=100)
        assert Model.CEREBRAS_GEMMA_4_31B.pricing is None
        assert usage.cost_micro_usd(Model.CEREBRAS_GEMMA_4_31B) is None

    def test_zero_usage_costs_zero(self) -> None:
        usage = Usage(input_tokens=0, output_tokens=0)
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5) == 0
