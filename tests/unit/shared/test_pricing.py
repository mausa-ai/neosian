"""Integer micro-USD pricing vocabulary (DESIGN §4, ECOSYSTEM §4)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from neosian import (
    MICRO_PER_USD,
    PRICES_FINGERPRINT,
    Model,
    ModelPricing,
    Provider,
    Usage,
    format_micro_usd,
)
from neosian._foundation.llm.base import ModelUsage
from neosian._foundation.shared.models import _prices_fingerprint

# The published USD/MTok rate card, as verified on PRICES_AS_OF — the
# no-rounding gate: every shipped int µ$ rate must equal its decimal USD
# price exactly. Order: (input, output, cache_read, cache_write). The door
# rows (DESIGN §19.5, §31) sit on the same card, standard tier.
_Card = tuple[str, str, str | None, str | None]
_USD_RATE_CARD: dict[Model, _Card] = {
    Model.GPT_6_ASTRA: ("10.00", "50.00", "1.00", "12.50"),
    Model.GPT_6_1_SOL: ("2.00", "10.00", "0.10", "2.50"),
    Model.GPT_6_SOL: ("2.00", "10.00", "0.20", "2.50"),
    Model.GPT_6_LUNA: ("0.10", "0.50", "0.01", "0.125"),
    Model.GPT_5_1: ("1.25", "10.00", "0.125", None),
    Model.CLAUDE_FABLE_5_1: ("10.00", "50.00", "0.25", "12.50"),
    Model.CLAUDE_OPUS_5_5: ("4.00", "20.00", "0.20", "5.00"),
    Model.CLAUDE_SONNET_5_5: ("2.00", "10.00", "0.20", "2.50"),
    Model.CLAUDE_SONNET_5: ("2.00", "10.00", "0.20", "2.50"),
    Model.CLAUDE_HAIKU_5_5: ("0.10", "0.50", "0.01", "0.125"),
    Model.CEREBRAS_GPT_OSS_120B: ("0.25", "0.69", None, None),
    Model.CEREBRAS_QWEN_3_8_27B: ("0.99", "1.49", None, None),
    Model.GROK_4_6: ("2.00", "6.00", "0.50", None),
    Model.GEMINI_3_8_FLASH: ("0.75", "3.75", "0.075", None),
    Model.GEMINI_3_7_FLASH: ("0.75", "3.75", "0.075", None),
    Model.KIMI_K3: ("3.00", "15.00", "0.30", None),
    Model.QWEN_3_8_MAX: ("2.00", "6.00", None, None),
}
# The long-prompt tier (#335): the threshold, then the same four columns.
_USD_LONG_PROMPT_CARD: dict[Model, tuple[int, _Card]] = {
    Model.CLAUDE_HAIKU_5_5: (100_000, ("0.50", "2.50", "0.05", "0.625")),
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
            for m in Model
            if m.pricing is not None and m.provider is not Provider.FAKE
        }
        assert priced == set(_USD_RATE_CARD)

    def test_long_prompt_cards_convert_exactly(self) -> None:
        for model, (over, (inp, out, read, write)) in _USD_LONG_PROMPT_CARD.items():
            pricing = model.pricing
            assert pricing is not None and pricing.long_prompt is not None, model
            assert pricing.long_prompt_tokens == over, model
            long = pricing.long_prompt
            assert long.input_per_mtok == _micro(inp), model
            assert long.output_per_mtok == _micro(out), model
            assert long.cache_read_per_mtok == _micro(read), model
            assert long.cache_write_per_mtok == _micro(write), model

    def test_long_prompt_cards_cover_every_tiered_row(self) -> None:
        tiered = {m for m in Model if m.pricing is not None and m.pricing.long_prompt}
        assert tiered == set(_USD_LONG_PROMPT_CARD)

    def test_fingerprint_matches_shipped_table(self) -> None:
        """Any price edit fails here until PRICES_FINGERPRINT (and with it
        PRICES_AS_OF) is bumped in the same commit."""
        assert _prices_fingerprint() == PRICES_FINGERPRINT


@pytest.mark.unit
class TestCostGoldenVectors:
    """Hand-computed ceiling-division vectors — the kit must agree on these."""

    def test_exact_division(self) -> None:
        # 10k in + 2k out + 50k cache-read on Sonnet 5 = exactly 50_000 µ$
        usage = Usage(
            input_tokens=10_000, output_tokens=2_000, cache_read_tokens=50_000
        )
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5) == 50_000

    def test_sol_61_uses_the_lower_cache_read_card(self) -> None:
        # 1M uncached + 1M read + 1M write + 1M output = $14.60.
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        assert usage.cost_micro_usd(Model.GPT_6_1_SOL) == 14_600_000
        assert (
            Usage(input_tokens=0, output_tokens=0, cache_read_tokens=1).cost_micro_usd(
                Model.GPT_6_1_SOL
            )
            == 1
        )

    def test_fractional_rounds_up(self) -> None:
        # 1234*250_000 + 567*690_000 = 699_730_000 → 699.73 µ$ → 700
        usage = Usage(input_tokens=1_234, output_tokens=567)
        assert usage.cost_micro_usd(Model.CEREBRAS_GPT_OSS_120B) == 700

    def test_a_cache_write_bills_the_five_minute_rate_by_default(self) -> None:
        # 100k cache-write on Sonnet 5 at 2.50 $/MTok = 250_000 µ$. The
        # suite had no cost vector covering a cache write before NC9.
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=100_000)
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5) == 250_000

    def test_an_openai_write_bills_its_carded_rate(self) -> None:
        # 100k cache-write on gpt-6-sol at 2.50 $/MTok = 250_000 µ$: OpenAI
        # bills a write at 1.25× input on GPT-5.6 and later (NW4), and the
        # card carries the column rather than falling back to input.
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=100_000)
        assert usage.cost_micro_usd(Model.GPT_6_SOL) == 250_000

    def test_an_hour_long_write_bills_twice_base(self) -> None:
        # The same 100k at 2x input (4.00 $/MTok) = 400_000 µ$ (#227).
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=100_000)
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5, cache_ttl="1h") == 400_000

    def test_the_ttl_moves_only_the_write(self) -> None:
        """Reads cost the same at either lifetime, and Anthropic reports no
        per-TTL split for them — so only the write rail moves."""
        usage = Usage(
            input_tokens=1_000,
            output_tokens=1_000,
            cache_read_tokens=1_000,
            cache_write_tokens=0,
        )
        assert usage.cost_micro_usd(Model.CLAUDE_SONNET_5) == usage.cost_micro_usd(
            Model.CLAUDE_SONNET_5, cache_ttl="1h"
        )

    def test_haiku_bills_the_base_card_up_to_the_threshold(self) -> None:
        # 100k in + 1k out on Haiku 5.5: the boundary is inclusive = 10_500 µ$
        usage = Usage(input_tokens=100_000, output_tokens=1_000)
        assert usage.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 10_500

    def test_haiku_bills_the_long_card_past_the_threshold(self) -> None:
        # 100_001 in + 1k out: 50_000_500_000 + 2_500_000_000 → 52_500.5 → 52_501
        usage = Usage(input_tokens=100_001, output_tokens=1_000)
        assert usage.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 52_501

    def test_cached_tokens_count_toward_the_prompt_length(self) -> None:
        # 1 in + 100k read: a 100_001-token prompt, so the long card
        # (1 × 0.50 + 100k × 0.05 = 5_000.5 → 5_001); 1_001 on the base card.
        usage = Usage(input_tokens=1, output_tokens=0, cache_read_tokens=100_000)
        assert usage.prompt_tokens == 100_001
        assert usage.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 5_001

    def test_the_long_card_derives_its_hour_write_too(self) -> None:
        # 200k cache-write on the long card: 0.625 → 125_000 µ$; the hour
        # at twice its input (1.00) → 200_000.
        usage = Usage(input_tokens=0, output_tokens=0, cache_write_tokens=200_000)
        assert usage.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 125_000
        assert usage.cost_micro_usd(Model.CLAUDE_HAIKU_5_5, cache_ttl="1h") == 200_000

    def test_a_summed_usage_never_undercounts_its_calls(self) -> None:
        """Two 60k calls bill 6_500 µ$ each on the base card; their sum
        straddles the threshold and bills 65_000 on the long card: an
        overcount, never an undercount. Equal when no call straddles."""
        call = Usage(input_tokens=60_000, output_tokens=1_000)
        assert call.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 6_500
        assert (call + call).cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 65_000
        short = Usage(input_tokens=10_000, output_tokens=0)
        assert short.cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 1_000
        assert (short + short).cost_micro_usd(Model.CLAUDE_HAIKU_5_5) == 2_000

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

    def test_the_hour_rate_is_twice_base_whatever_the_card_says(self) -> None:
        """Derived, not carded: the 1h premium is a published multiple of a
        rate the fingerprint already seals, so no column enters the card."""
        pricing = ModelPricing(
            input_per_mtok=100, output_per_mtok=200, cache_write_per_mtok=125
        )
        assert pricing.effective_cache_write_per_mtok == 125
        assert pricing.effective_cache_write_1h_per_mtok == 200

    def test_for_prompt_picks_the_long_card_past_the_threshold(self) -> None:
        long = ModelPricing(input_per_mtok=500, output_per_mtok=2_500)
        pricing = ModelPricing(
            input_per_mtok=100,
            output_per_mtok=500,
            long_prompt_tokens=100,
            long_prompt=long,
        )
        assert pricing.for_prompt(0) is pricing
        assert pricing.for_prompt(100) is pricing
        assert pricing.for_prompt(101) is long
        flat = ModelPricing(input_per_mtok=100, output_per_mtok=500)
        assert flat.for_prompt(10**9) is flat

    def test_a_long_card_travels_with_its_threshold_one_tier_deep(self) -> None:
        with pytest.raises(ValueError, match="travel together"):
            ModelPricing(input_per_mtok=1, output_per_mtok=2, long_prompt_tokens=100)
        long = ModelPricing(input_per_mtok=5, output_per_mtok=10)
        with pytest.raises(ValueError, match="travel together"):
            ModelPricing(input_per_mtok=1, output_per_mtok=2, long_prompt=long)
        nested = ModelPricing(
            input_per_mtok=5, output_per_mtok=10, long_prompt_tokens=1, long_prompt=long
        )
        with pytest.raises(ValueError, match="one tier deep"):
            ModelPricing(
                input_per_mtok=1,
                output_per_mtok=2,
                long_prompt_tokens=1,
                long_prompt=nested,
            )

    def test_a_long_card_is_never_cheaper_on_any_column(self) -> None:
        for cheaper in (
            ModelPricing(input_per_mtok=1, output_per_mtok=10),
            ModelPricing(input_per_mtok=5, output_per_mtok=1),
            ModelPricing(input_per_mtok=5, output_per_mtok=10, cache_read_per_mtok=1),
            ModelPricing(input_per_mtok=5, output_per_mtok=10, cache_write_per_mtok=1),
        ):
            with pytest.raises(ValueError, match="never cheaper"):
                ModelPricing(
                    input_per_mtok=2,
                    output_per_mtok=4,
                    long_prompt_tokens=1,
                    long_prompt=cheaper,
                )

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
