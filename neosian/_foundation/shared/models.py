"""The model registry: providers, models, capabilities, the rate card.

Money is integer micro-USD (ECOSYSTEM §4). PRICES_FINGERPRINT gates the
shipped rate card: a price edit fails CI until the fingerprint and
PRICES_AS_OF move in the same commit (DESIGN §4).
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import Enum
from functools import partial
from typing import Final

from neosian._foundation.shared.catalog import GEMINI, XAI, OpenAICompatible

# =============================================================================
# Provider and Model Enums
# =============================================================================


class Provider(str, Enum):
    """LLM Provider identifiers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CEREBRAS = "cerebras"
    # Registered models' shared row (DESIGN §19): a client is built from
    # the model's door, never from this row alone.
    OPENAI_COMPATIBLE = "openai_compatible"
    FAKE = "fake"


# Date the pricing table below was last verified against provider price lists.
PRICES_AS_OF = "2026-09-10"

# Integer micro-USD per USD — money is int µ$ everywhere (ECOSYSTEM §4);
# floats exist only at display edges (format_micro_usd).
MICRO_PER_USD: Final = 1_000_000


def format_micro_usd(micro: int, *, places: int = 4) -> str:
    """Render integer micro-USD as a dollar string — the display edge.

    The one place a float touches money; it never re-enters the vocabulary.
    """
    return f"${micro / MICRO_PER_USD:.{places}f}"


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """List prices in integer micro-USD per million tokens (ECOSYSTEM §4).

    Approximate, for observability/cost-tracking — not a billing source.
    None cache rates mean "no separate published rate"; the effective_*
    properties fall back to the input rate (conservative upper bound).
    """

    input_per_mtok: int
    output_per_mtok: int
    cache_read_per_mtok: int | None = None
    cache_write_per_mtok: int | None = None

    @property
    def effective_cache_read_per_mtok(self) -> int:
        """Cache-read rate, falling back to the input rate."""
        if self.cache_read_per_mtok is not None:
            return self.cache_read_per_mtok
        return self.input_per_mtok

    @property
    def effective_cache_write_per_mtok(self) -> int:
        """Cache-write rate, falling back to the input rate."""
        if self.cache_write_per_mtok is not None:
            return self.cache_write_per_mtok
        return self.input_per_mtok


@dataclass(frozen=True)
class ModelSpec:
    """Immutable specification for a model's capabilities.

    supports_images / supports_documents describe what neosian's converters
    implement, not the raw provider capability (e.g. GPT-5 has vision
    upstream, but neosian's OpenAI converter does not — so it stays False).

    pricing is None for models without verified list prices;
    Usage.cost_micro_usd() returns None for those.
    """

    provider: Provider
    context_window: int
    max_output_tokens: int
    supports_reasoning: bool = False
    supports_images: bool = False
    supports_documents: bool = False
    supports_max_effort: bool = False
    # Anthropic's compact-2026-01-12 beta — a provider check would be
    # wrong: Haiku 4.5 is Anthropic and outside the support set (N4).
    supports_compaction_blocks: bool = False
    pricing: ModelPricing | None = None
    # The door a compat row is served through (DESIGN §31): set on the
    # shipped door rows and on every registered model, None on a provider
    # adapter's row. Not part of the rate card the fingerprint seals.
    door: OpenAICompatible | None = None
    # The catalog clock (DESIGN §31, ROADMAP §NW): the provider's shutdown
    # or not-sooner-than date, and the date a stated card stops holding.
    # A keyless test fails `make test` inside 30 days of either.
    retires: date | None = None
    card_until: date | None = None


# Model specs registry (populated after Model enum is defined)
_MODEL_SPECS: dict[str, ModelSpec] = {}


class ReasoningEffort(str, Enum):
    """Reasoning effort level for supported models.

    Controls how many reasoning tokens the model uses.
    Supported by GPT-OSS models (Cerebras), GPT-5 models (OpenAI),
    and reasoning-capable Claude models (Anthropic).
    Note: MAX is only passed through for models whose spec sets
    supports_max_effort; every client downgrades it to HIGH with a
    warning otherwise.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class Model(str, Enum):
    """Supported LLM models."""

    # OpenAI
    GPT_5_6_SOL = "gpt-5.6-sol"
    GPT_5_6_TERRA = "gpt-5.6-terra"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    GPT_5_1 = "gpt-5.1-2025-11-13"

    # Anthropic
    CLAUDE_FABLE_5_1 = "claude-fable-5-1"
    CLAUDE_OPUS_5 = "claude-opus-5"
    CLAUDE_SONNET_5 = "claude-sonnet-5"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5-20251001"

    # Cerebras
    CEREBRAS_GPT_OSS_120B = "gpt-oss-120b"
    CEREBRAS_QWEN_3_8_27B = "qwen-3.8-27b"

    # The shipped door rows (DESIGN §19.5, §31): first-party, no client of
    # their own, served through the doors in catalog.py.
    GROK_4_6 = "grok-4.6"
    GEMINI_3_8_FLASH = "gemini-3.8-flash"
    GEMINI_3_7_FLASH = "gemini-3.7-flash"

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


# OpenAI — the GPT-5.6 family (developers.openai.com/api/docs/pricing,
# 2026-09-10): standard tier sealed; input above 272K bills at 2× in and
# 1.5× out. Every 5.6 row takes `reasoning_effort=max`.
_GPT_5_6 = partial(
    ModelSpec,
    provider=Provider.OPENAI,
    context_window=1_050_000,
    max_output_tokens=128_000,
    supports_reasoning=True,
    supports_max_effort=True,
)
_MODEL_SPECS[Model.GPT_5_6_SOL.value] = _GPT_5_6(
    pricing=ModelPricing(
        input_per_mtok=4_000_000,
        output_per_mtok=20_000_000,
        cache_read_per_mtok=400_000,
    ),
)
_MODEL_SPECS[Model.GPT_5_6_TERRA.value] = _GPT_5_6(
    pricing=ModelPricing(
        input_per_mtok=2_000_000,
        output_per_mtok=12_000_000,
        cache_read_per_mtok=200_000,
    ),
)
_MODEL_SPECS[Model.GPT_5_6_LUNA.value] = _GPT_5_6(
    pricing=ModelPricing(
        input_per_mtok=200_000,
        output_per_mtok=1_200_000,
        cache_read_per_mtok=20_000,
    ),
)
# Not on OpenAI's deprecation list (2026-09-10); rides the catalog probe.
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
# /model-deprecations, 2026-09-10): `retires` is the published
# not-sooner-than floor.
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
)
_MODEL_SPECS[Model.CLAUDE_FABLE_5_1.value] = _CLAUDE_5(
    # Its own cache-read rate: 0.025× of input, not the 0.1× of the rest.
    pricing=ModelPricing(
        input_per_mtok=10_000_000,
        output_per_mtok=50_000_000,
        cache_read_per_mtok=250_000,
        cache_write_per_mtok=12_500_000,
    ),
    retires=date(2027, 9, 1),
)
_MODEL_SPECS[Model.CLAUDE_OPUS_5.value] = _CLAUDE_5(
    pricing=ModelPricing(
        input_per_mtok=5_000_000,
        output_per_mtok=25_000_000,
        cache_read_per_mtok=500_000,
        cache_write_per_mtok=6_250_000,
    ),
    retires=date(2027, 7, 24),
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
_MODEL_SPECS[Model.CLAUDE_HAIKU_4_5.value] = ModelSpec(
    provider=Provider.ANTHROPIC,
    context_window=200_000,
    max_output_tokens=64_000,
    supports_images=True,
    supports_documents=True,
    pricing=ModelPricing(
        input_per_mtok=1_000_000,
        output_per_mtok=5_000_000,
        cache_read_per_mtok=100_000,
        cache_write_per_mtok=1_250_000,
    ),
    retires=date(2026, 10, 15),
)

# Cerebras (inference-docs.cerebras.ai/models, 2026-09-10): the public
# catalog is these two; the paid tier's limits.
_MODEL_SPECS[Model.CEREBRAS_GPT_OSS_120B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=40_960,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=250_000, output_per_mtok=690_000),
)
_MODEL_SPECS[Model.CEREBRAS_QWEN_3_8_27B.value] = ModelSpec(
    provider=Provider.CEREBRAS,
    context_window=131_072,
    max_output_tokens=40_960,
    supports_reasoning=True,
    pricing=ModelPricing(input_per_mtok=990_000, output_per_mtok=1_490_000),
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

# Default models per provider
DEFAULT_MODELS: dict[Provider, Model] = {
    Provider.OPENAI: Model.GPT_5_6_SOL,
    Provider.ANTHROPIC: Model.CLAUDE_SONNET_5,
    Provider.CEREBRAS: Model.CEREBRAS_GPT_OSS_120B,
    Provider.FAKE: Model.FAKE,
}


def _prices_fingerprint() -> str:
    """Canonical sha256 of the shipped rate card + its as-of date.

    The card is the enum's one table — every shipped row, door rows
    included (§31).

    A unit test recomputes this against PRICES_FINGERPRINT, so any price
    edit fails CI until the fingerprint (and, with it, PRICES_AS_OF) is
    bumped in the same commit — a gate, not a promise.
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


PRICES_FINGERPRINT = "2933a70ba5b3fba4e114cecd21fac80bea66955b8817ade83e90c5b949b2e6b9"
