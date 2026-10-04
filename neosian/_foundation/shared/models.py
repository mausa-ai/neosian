"""The model registry: providers, models, capabilities, the rate card.

Money is integer micro-USD (ECOSYSTEM §4). PRICES_FINGERPRINT gates the
shipped rate card: a price edit fails CI until the fingerprint and
PRICES_AS_OF move in the same commit (DESIGN §4).
"""

import hashlib
from datetime import date
from enum import Enum
from functools import partial

from neosian._foundation.shared.catalog import (
    CEREBRAS,
    GEMINI,
    KIMI,
    QWEN,
    XAI,
    OpenAICompatible,
)

# =============================================================================
# Provider and Model Enums
# =============================================================================
from neosian._foundation.shared.model_spec import (
    MICRO_PER_USD as MICRO_PER_USD,
    ModelPricing as ModelPricing,
    ModelSpec as ModelSpec,
    Provider as Provider,
    ReasoningEffort as ReasoningEffort,
    format_micro_usd as format_micro_usd,
)

PRICES_AS_OF = "2026-10-04"
_MODEL_SPECS: dict[str, ModelSpec] = {}


class Model(str, Enum):
    """Supported LLM models."""

    # OpenAI
    GPT_6_ASTRA = "gpt-6-astra"
    GPT_6_1_SOL = "gpt-6.1-sol"
    GPT_6_SOL = "gpt-6-sol"
    GPT_6_LUNA = "gpt-6-luna"
    GPT_5_1 = "gpt-5.1-2025-11-13"

    # Anthropic
    CLAUDE_FABLE_5_1 = "claude-fable-5-1"
    CLAUDE_OPUS_5_5 = "claude-opus-5-5"
    CLAUDE_SONNET_5_5 = "claude-sonnet-5-5"
    CLAUDE_SONNET_5 = "claude-sonnet-5"

    # Cerebras
    CEREBRAS_GPT_OSS_120B = "gpt-oss-120b"
    CEREBRAS_QWEN_3_8_27B = "qwen-3.8-27b"

    # The shipped door rows (DESIGN §19.5, §31): first-party, no client of
    # their own, served through the doors in catalog.py.
    GROK_4_6 = "grok-4.6"
    GEMINI_3_8_FLASH = "gemini-3.8-flash"
    GEMINI_3_7_FLASH = "gemini-3.7-flash"
    KIMI_K3 = "kimi-k3"
    QWEN_3_8_MAX = "qwen3.8-max"

    # Fake (deterministic, keyless — public surface in neosian.fake)
    FAKE = "fake"
    FAKE_SMALL = "fake-small"
    FAKE_REASONING = "fake-reasoning"

    @property
    def spec(self) -> ModelSpec:
        """Get the full specification for this model."""
        return _MODEL_SPECS[self.value]

    @property
    def provider(self) -> Provider:
        """Get the provider for this model."""
        return _MODEL_SPECS[self.value].provider

    @property
    def max_output_tokens(self) -> int:
        """Get max output tokens (API ceiling) for this model."""
        return _MODEL_SPECS[self.value].max_output_tokens

    @property
    def context_window(self) -> int:
        """Get context window size for this model."""
        return _MODEL_SPECS[self.value].context_window

    @property
    def supports_reasoning(self) -> bool:
        """Check if this model supports reasoning_effort parameter."""
        return _MODEL_SPECS[self.value].supports_reasoning

    @property
    def supports_images(self) -> bool:
        """Check if neosian's converter supports image content for this model."""
        return _MODEL_SPECS[self.value].supports_images

    @property
    def supports_documents(self) -> bool:
        """Check if neosian's converter supports document content for this model."""
        return _MODEL_SPECS[self.value].supports_documents

    @property
    def supports_max_effort(self) -> bool:
        """Check if this model accepts reasoning_effort=MAX without downgrade."""
        return _MODEL_SPECS[self.value].supports_max_effort

    @property
    def supports_xhigh_effort(self) -> bool:
        """Whether this model accepts xhigh without translation."""
        return self.spec.supports_xhigh_effort

    @property
    def supports_no_effort(self) -> bool:
        """Whether explicit reasoning effort none is supported."""
        return self.spec.supports_no_effort

    @property
    def supports_forced_tool_choice(self) -> bool:
        """Check if this model accepts a forced tool_choice (required / tool)."""
        return _MODEL_SPECS[self.value].supports_forced_tool_choice

    @property
    def supports_compaction_blocks(self) -> bool:
        """Check if this model supports Anthropic server-side compaction."""
        return _MODEL_SPECS[self.value].supports_compaction_blocks

    @property
    def pricing(self) -> "ModelPricing | None":
        """Get list pricing for this model (None if not verified)."""
        return _MODEL_SPECS[self.value].pricing

    @property
    def door(self) -> OpenAICompatible | None:
        """The door this model is served through, if it is a compat row."""
        return _MODEL_SPECS[self.value].door


# OpenAI (developers.openai.com/api/docs/pricing, /models/gpt-6-sol,
# 2026-09-26), on the Responses wire (§31.5): the standard tier sealed
# (above 272K input: 2× in, cached and write, 1.5× out); a write bills
# 1.25× input and the codecs split it out of the input count. Every row
# takes `reasoning_effort=max`; Astra rides the probe (#256), Sol is the
# default and the measured row (NW4, #291).
_GPT_6 = partial(
    ModelSpec,
    provider=Provider.OPENAI,
    context_window=1_050_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_max_effort=True,
    supports_xhigh_effort=True,
)
_MODEL_SPECS[Model.GPT_6_ASTRA.value] = _GPT_6(
    pricing=ModelPricing(
        input_per_mtok=10_000_000,
        output_per_mtok=50_000_000,
        cache_read_per_mtok=1_000_000,
        cache_write_per_mtok=12_500_000,
    ),
)
_MODEL_SPECS[Model.GPT_6_1_SOL.value] = _GPT_6(
    pricing=ModelPricing(2_000_000, 10_000_000, 100_000, 2_500_000),
)
_MODEL_SPECS[Model.GPT_6_SOL.value] = _GPT_6(
    supports_no_effort=True,
    pricing=ModelPricing(
        input_per_mtok=2_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=200_000,
        cache_write_per_mtok=2_500_000,
    ),
)
_MODEL_SPECS[Model.GPT_6_LUNA.value] = _GPT_6(
    supports_no_effort=True,
    pricing=ModelPricing(
        input_per_mtok=100_000,
        output_per_mtok=500_000,
        cache_read_per_mtok=10_000,
        cache_write_per_mtok=125_000,
    ),
)
# No deprecation notice (2026-09-26) and no cache-write charge; on the probe.
_MODEL_SPECS[Model.GPT_5_1.value] = ModelSpec(
    provider=Provider.OPENAI,
    context_window=400_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=1_250_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=125_000,
    ),
)

# Anthropic (platform.claude.com/docs/en/about-claude/pricing and
# /model-deprecations, 2026-09-26): `retires` is the not-sooner-than
# floor; Opus 5.5 and Fable 5.1 take no forced `tool_choice` (#292).
_CLAUDE_5 = partial(
    ModelSpec,
    provider=Provider.ANTHROPIC,
    context_window=1_000_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_images=True,
    supports_documents=True,
    supports_max_effort=True,
    supports_compaction_blocks=True,
    supports_xhigh_effort=True,
)
_MODEL_SPECS[Model.CLAUDE_FABLE_5_1.value] = _CLAUDE_5(
    # Its own cache-read rate: 0.025× of input, not the 0.1× of the rest.
    pricing=ModelPricing(
        input_per_mtok=10_000_000,
        output_per_mtok=50_000_000,
        cache_read_per_mtok=250_000,
        cache_write_per_mtok=12_500_000,
    ),
    supports_forced_tool_choice=False,
    retires=date(2027, 9, 1),
)
_MODEL_SPECS[Model.CLAUDE_OPUS_5_5.value] = _CLAUDE_5(
    # Its own cache-read rate: 0.05× of input.
    pricing=ModelPricing(
        input_per_mtok=4_000_000,
        output_per_mtok=20_000_000,
        cache_read_per_mtok=200_000,
        cache_write_per_mtok=5_000_000,
    ),
    supports_forced_tool_choice=False,
    retires=date(2027, 9, 22),
)
_MODEL_SPECS[Model.CLAUDE_SONNET_5_5.value] = _CLAUDE_5(
    pricing=ModelPricing(2_000_000, 10_000_000, 200_000, 2_500_000),
    supports_forced_tool_choice=False,
    retires=date(2027, 9, 28),
)
_MODEL_SPECS[Model.CLAUDE_SONNET_5.value] = _CLAUDE_5(
    # The announced 2026-09-01 rise to $3/$15 did not occur (re-verified
    # 2026-09-10); the card is $2/$10.
    pricing=ModelPricing(
        input_per_mtok=2_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=200_000,
        cache_write_per_mtok=2_500_000,
    ),
    retires=date(2027, 6, 30),
)

# Cerebras (inference-docs.cerebras.ai/models, 2026-09-10): the public
# catalog is these two; the paid tier's limits. Served through the door
# since NC7 (ledger #218); the provider row stays the rows' label.
_MODEL_SPECS[Model.CEREBRAS_GPT_OSS_120B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=40_960,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=250_000, output_per_mtok=690_000),
    door=CEREBRAS,
)
_MODEL_SPECS[Model.CEREBRAS_QWEN_3_8_27B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=40_960,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=990_000, output_per_mtok=1_490_000),
    door=CEREBRAS,
)

# Fake — deterministic keyless models (ECOSYSTEM §7). The capability split
# (FAKE carries media, FAKE_SMALL none; FAKE_SMALL's window is small) makes
# capability-aware fallback and context policy testable without keys, and
# the round, distinct rates make per-model cost attribution hand-computable.
_MODEL_SPECS[Model.FAKE.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=128_000,
    max_output_tokens=8_192,
    supports_images=True,
    supports_documents=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
)
_MODEL_SPECS[Model.FAKE_SMALL.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=8_192,
    max_output_tokens=8_192,
    pricing=ModelPricing(
        input_per_mtok=100_000,
        output_per_mtok=1_000_000,
        cache_read_per_mtok=10_000,
        cache_write_per_mtok=125_000,
    ),
)
_MODEL_SPECS[Model.FAKE_REASONING.value] = ModelSpec(
    provider=Provider.FAKE,
    context_window=128_000,
    max_output_tokens=8_192,
    supports_reasoning=True,
    supports_max_effort=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=10_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
)

# The shipped door rows (DESIGN §19.5, §31): priced here so the fingerprint
# seals them. Where a card is tiered, the standard ≤200k tier is the sealed
# number (docs.x.ai/docs/models; ai.google.dev/gemini-api/docs/pricing).
_MODEL_SPECS[Model.GROK_4_6.value] = ModelSpec(
    provider=Provider.OPENAI_COMPATIBLE,
    context_window=500_000,
    max_output_tokens=32_768,  # unpublished — a conservative ceiling
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=2_000_000,
        output_per_mtok=6_000_000,
        cache_read_per_mtok=500_000,
    ),
    door=XAI,
)
# Gemini's introductory card holds through 2026-12-31 and doubles after;
# 3.8 is the measured row, 3.7 stays on the probe.
_GEMINI_FLASH = partial(
    ModelSpec,
    provider=Provider.OPENAI_COMPATIBLE,
    context_window=1_048_576,
    max_output_tokens=65_536,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=750_000,
        output_per_mtok=3_750_000,
        cache_read_per_mtok=75_000,
    ),
    door=GEMINI,
    card_until=date(2026, 12, 31),
)
_MODEL_SPECS[Model.GEMINI_3_8_FLASH.value] = _GEMINI_FLASH()
_MODEL_SPECS[Model.GEMINI_3_7_FLASH.value] = _GEMINI_FLASH()
# Moonshot (platform.kimi.ai/docs/pricing/chat, 2026-09-15): one flat card
# across the window; 131,072 is the documented default completion ceiling.
_MODEL_SPECS[Model.KIMI_K3.value] = ModelSpec(
    provider=Provider.OPENAI_COMPATIBLE,
    context_window=1_048_576,
    max_output_tokens=131_072,
    supports_reasoning=True,
    pricing=ModelPricing(
        input_per_mtok=3_000_000,
        output_per_mtok=15_000_000,
        cache_read_per_mtok=300_000,
    ),
    door=KIMI,
)
# Alibaba Model Studio, Singapore (alibabacloud.com/help/en/model-studio/
# model-pricing, 2026-09-16): $2/$6 flat across the 1M window; the cache-hit
# rate is published only in the console, so none is sealed (the input rate
# is the conservative fallback); 131,072 the documented output ceiling.
_MODEL_SPECS[Model.QWEN_3_8_MAX.value] = ModelSpec(
    provider=Provider.OPENAI_COMPATIBLE,
    context_window=1_000_000,
    max_output_tokens=131_072,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=2_000_000, output_per_mtok=6_000_000),
    door=QWEN,
)

# Default models per provider
DEFAULT_MODELS: dict[Provider, Model] = {
    Provider.OPENAI: Model.GPT_6_SOL,
    Provider.ANTHROPIC: Model.CLAUDE_SONNET_5,
    Provider.CEREBRAS: Model.CEREBRAS_GPT_OSS_120B,
    Provider.FAKE: Model.FAKE,
}


def _prices_fingerprint() -> str:
    """Canonical sha256 of the shipped rate card + its as-of date.

    The card is the enum's one table — every shipped row, door rows
    included (§31). A unit test recomputes this against PRICES_FINGERPRINT,
    so any price edit fails CI until the fingerprint (and, with it,
    PRICES_AS_OF) is bumped in the same commit — a gate, not a promise.
    """
    lines = [f"as_of:{PRICES_AS_OF}"]
    for model_id in sorted(_MODEL_SPECS):
        spec = _MODEL_SPECS[model_id]
        # Fake-model rates are test fixtures, not provider prices.
        if spec.pricing is None or spec.provider is Provider.FAKE:
            continue
        pricing = spec.pricing
        lines.append(
            f"{model_id}:{pricing.input_per_mtok}:{pricing.output_per_mtok}"
            f":{pricing.cache_read_per_mtok}:{pricing.cache_write_per_mtok}"
        )
    return hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()


PRICES_FINGERPRINT = "916bc097749a495d34a01fa470c898cc0d342fce74251feacf3cd2e73e909362"
