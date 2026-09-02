"""Integer micro-USD pricing vocabulary (DESIGN §4, ECOSYSTEM §4)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from neosian import (
    MICRO_PER_USD,
    PRICES_FINGERPRINT,
    AnyModel,
    Model,
    ModelPricing,
    Provider,
    Usage,
    format_micro_usd,
)
from neosian._foundation.llm.base import ModelUsage
from neosian._foundation.shared.catalog import CATALOG, GEMINI_3_7_FLASH, GROK_4_6
from neosian._foundation.shared.models import _prices_fingerprint

# The published USD/MTok rate card, as verified on PRICES_AS_OF — the
# no-rounding gate: every shipped int µ$ rate must equal its decimal USD
# price exactly. Order: (input, output, cache_read, cache_write). The
# catalog rows (DESIGN §19.5) sit on the same card, standard tier.
_USD_RATE_CARD: dict[AnyModel, tuple[str, str, str | None, str | None]] = {
    Model.GPT_5_1: ("1.25", "10.00", "0.125", None),
    Model.GPT_5_MINI: ("0.25", "2.00", "0.025", None),
    Model.GPT_5_NANO: ("0.05", "0.40", "0.005", None),
    Model.GPT_5_PRO: ("15.00", "120.00", None, None),
    Model.CLAUDE_OPUS_5: ("5.00", "25.00", "0.50", "6.25"),
    Model.CLAUDE_OPUS_4_6: ("5.00", "25.00", "0.50", "6.25"),
    Model.CLAUDE_SONNET_5: ("3.00", "15.00", "0.30", "3.75"),
    Model.CLAUDE_HAIKU_4_5: ("1.00", "5.00", "0.10", "1.25"),
    Model.CEREBRAS_GPT_OSS_120B: ("0.25", "0.69", None, None),
    GROK_4_6: ("2.00", "6.00", "0.50", None),
    GEMINI_3_7_FLASH: ("0.75", "3.75", "0.075", None),
}


def _micro(usd: str | None) -> int | None:
    if usd is None:
        return None
    micro = Decimal(usd) * MICRO_PER_USD
    assert micro == micro.to_integral_value(), f"{usd} USD/MTok needs rounding"
    return int(micro)


@pytest.mark.unit
class TestRateCard:
    """The shipped int µ$ rates convert exactly from the published prices."""

    def test_no_shipped_price_required_rounding(self) -> None:
        for model, (inp, out, read, write) in _USD_RATE_CARD.items():
            pricing = model.pricing
            assert pricing is not None, model
            assert pricing.input_per_mtok == _micro(inp), model
            assert pricing.output_per_mtok == _micro(out), model
            assert pricing.cache_read_per_mtok == _micro(read), model
            assert pricing.cache_write_per_mtok == _micro(write), model

    def test_rate_card_covers_every_priced_model(self) -> None:
        """A new priced model must enter the golden card in the same diff.

        Fake-model rates are test fixtures, not provider prices — excluded
        here exactly as in the fingerprint.
        """
        priced = {
            m
            for m in (*Model, *CATALOG)
            if m.pricing is not None and m.provider is not Provider.FAKE
        }
        assert priced == set(_USD_RATE_CARD)

    def test_fingerprint_matches_shipped_table(self) -> None:
        """Any price edit fails here until PRICES_FINGERPRINT (and with it
        PRICES_AS_OF) is bumped in the same commit."""
        assert _prices_fingerprint() == PRICES_FINGERPRINT


@pytest.mark.unit
class TestCostGoldenVectors:
    """Hand-computed ceiling-division vectors — the kit must agree on these."""

    def test_exact_division(self) -> None:
        # 10k in + 2k out + 50k cache-read on Sonnet 5 = exactly 75_000 µ$
        usage = Usage(
            input_tokens=10_000, output_tokens=2_000, cache_read_tokens=50_000
        )
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5) == 75_000

    def test_fractional_rounds_up(self) -> None:
        # 1234*250_000 + 567*690_000 = 699_730_000 → 699.73 µ$ → 700
        usage = Usage(input_tokens=1_234, output_tokens=567)
        assert usage.cost_micro_usd(Model.CEREBRAS_GPT_OSS_120B) == 700

    def test_sub_micro_usd_bills_one(self) -> None:
        # 1 in + 1 out on Cerebras 120B = 940_000 / 1e6 = 0.94 µ$ → 1
        usage = Usage(input_tokens=1, output_tokens=1)
        assert usage.cost_micro_usd(Model.CEREBRAS_GPT_OSS_120B) == 1


@pytest.mark.unit
class TestFormatMicroUsd:
    """The display edge — the only float/str crossing for money."""

    def test_default_four_places(self) -> None:
        assert format_micro_usd(600) == "$0.0006"

    def test_whole_dollars(self) -> None:
        assert format_micro_usd(36_750_000) == "$36.7500"

    def test_places_override(self) -> None:
        assert format_micro_usd(36_750_000, places=2) == "$36.75"

    def test_zero(self) -> None:
        assert format_micro_usd(0) == "$0.0000"


@pytest.mark.unit
class TestValueObjects:
    """Usage / ModelPricing / ModelUsage are frozen, slotted value types."""

    def test_usage_is_frozen_and_slotted(self) -> None:
        usage = Usage(input_tokens=1, output_tokens=2)
        with pytest.raises(FrozenInstanceError):
            usage.input_tokens = 3  # type: ignore[misc]
        assert not hasattr(usage, "__dict__")

    def test_model_pricing_is_frozen_and_slotted(self) -> None:
        pricing = ModelPricing(input_per_mtok=1, output_per_mtok=2)
        with pytest.raises(FrozenInstanceError):
            pricing.input_per_mtok = 3  # type: ignore[misc]
        assert not hasattr(pricing, "__dict__")

    def test_effective_cache_rates_fall_back_to_input(self) -> None:
        bare = ModelPricing(input_per_mtok=100, output_per_mtok=200)
        assert bare.effective_cache_read_per_mtok == 100
        assert bare.effective_cache_write_per_mtok == 100
        full = ModelPricing(
            input_per_mtok=100,
            output_per_mtok=200,
            cache_read_per_mtok=7,
            cache_write_per_mtok=9,
        )
        assert full.effective_cache_read_per_mtok == 7
        assert full.effective_cache_write_per_mtok == 9

    def test_model_usage(self) -> None:
        entry = ModelUsage(model="fake-1", usage=Usage(input_tokens=1, output_tokens=2))
        assert entry.model == "fake-1"
        assert entry.usage.total_tokens == 3
        with pytest.raises(FrozenInstanceError):
            entry.model = "other"  # type: ignore[misc]
