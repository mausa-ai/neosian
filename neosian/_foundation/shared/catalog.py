"""The shipped OpenAI-compatible rows (DESIGN §19.5).

First-party models without a client of their own: a door plus a spec
from `models.CATALOG_SPECS`, where the fingerprint seals the price. Each
registers once at `import neosian`, so `lookup_model`, the picker, the
eval axis and the memory CLI reach it like a user's registration. A row
is earned by NW's gate — green over dispatched runs of the shipped pack
(§19.7, ledger #124) — and a red one exits whole.
"""

from neosian._foundation.shared.models import CATALOG_SPECS
from neosian._foundation.shared.registry import (
    OpenAICompatible,
    RegisteredModel,
    _register,
)

XAI = OpenAICompatible(
    name="xai",
    api_key_env="XAI_API_KEY",
    base_url="https://api.x.ai/v1",
    temperature=True,
    reasoning_field="reasoning_content",
)


GEMINI = OpenAICompatible(
    # The compat endpoint; thoughts ride ToolCall.extra (§19.3), no field.
    name="gemini",
    api_key_env="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    temperature=True,
)


def _row(value: str, door: OpenAICompatible) -> RegisteredModel:
    return _register(RegisteredModel(value, CATALOG_SPECS[value], door))


GROK_4_6 = _row("grok-4.6", XAI)
GEMINI_3_7_FLASH = _row("gemini-3.7-flash", GEMINI)

CATALOG: tuple[RegisteredModel, ...] = (GROK_4_6, GEMINI_3_7_FLASH)
