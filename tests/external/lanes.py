"""The door lanes of the external tier: shipped catalog rows and the
candidates still at NW's gate (DESIGN §19.7, NC2 slice B).

One shape either way — the same probes, the same pack, the same pacer. A
shipped row is the catalog's own object; a candidate is built here and
registered inside a test (`pytest -m 'not external'` imports this tree
at collection, and a module-level registration would leak past the unit
tier's "nothing beyond the shipped set" pins — `_register` is write-once
idempotent, so every test may call `registered()`). A candidate carries
no pricing: the number is sealed at promotion, where the fingerprint
takes it. `requests_per_minute` is the lane's account tier, found by the
first probes and paced in `pacing.py` — never a door knob (a limit is an
account property, not a dialect).
"""

from dataclasses import dataclass

from neosian import OpenAICompatible, Provider, RegisteredModel
from neosian._foundation.shared.catalog import GEMINI_3_7_FLASH, GROK_4_6
from neosian._foundation.shared.models import ModelSpec
from neosian._foundation.shared.registry import _register


@dataclass(frozen=True, slots=True, kw_only=True)
class Lane:
    model: RegisteredModel
    requests_per_minute: int | None = None  # the account's tier; see pacing.py

    @property
    def door(self) -> OpenAICompatible:
        return self.model.door

    @property
    def name(self) -> str:
        """The suite, its marker, the CI matrix entry — and the door."""
        return self.door.name

    @property
    def key_fixture(self) -> str:
        return f"{self.name}_api_key"

    def registered(self) -> RegisteredModel:
        """The catalog row itself; a candidate registers for this test."""
        return _register(self.model)


def _candidate(
    value: str,
    door: OpenAICompatible,
    *,
    context_window: int,
    max_output_tokens: int,
    supports_reasoning: bool = False,
) -> RegisteredModel:
    spec = ModelSpec(
        provider=Provider.OPENAI_COMPATIBLE,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_reasoning=supports_reasoning,
    )
    return RegisteredModel(value, spec, door)


XAI = Lane(model=GROK_4_6)

GEMINI = Lane(
    model=GEMINI_3_7_FLASH,
    requests_per_minute=5,  # the project's tier, per model (probed 2026-09-01)
)

KIMI = Lane(
    # Always reasons; the docs say "do not set temperature".
    model=_candidate(
        "kimi-k3",
        OpenAICompatible(
            name="kimi",
            api_key_env="MOONSHOT_API_KEY",
            base_url="https://api.moonshot.ai/v1",
            reasoning_field="reasoning_content",
        ),
        context_window=1_048_576,
        max_output_tokens=131_072,  # unpublished — a conservative ceiling
        supports_reasoning=True,
    ),
    requests_per_minute=3,  # the organisation's tier (probed 2026-09-01)
)

LANES: tuple[Lane, ...] = (XAI, GEMINI, KIMI)
