"""The playground picker lists registered models under their door
(DESIGN §19) — and shows no door row while nothing is registered."""

import pytest

from neosian import OpenAICompatible, Provider, register_model
from neosian._cli.models import (
    display_name,
    get_available_providers,
    get_models_for_provider,
)

XAI = OpenAICompatible(
    name="xai", api_key_env="XAI_API_KEY", base_url="https://api.x.ai/v1"
)


@pytest.mark.unit
class TestPickerDoors:
    def test_no_registrations_no_door_row(self) -> None:
        assert Provider.OPENAI_COMPATIBLE not in [
            p for p, _ in get_available_providers()
        ]
        assert get_models_for_provider(Provider.OPENAI_COMPATIBLE) == []

    def test_registered_models_sit_under_the_door_row_labeled_by_door(self) -> None:
        grok = register_model(
            "grok-4", provider=XAI, context_window=131_072, max_output_tokens=16_384
        )
        assert get_models_for_provider(Provider.OPENAI_COMPATIBLE) == [
            (grok, "xai/grok-4")
        ]
        assert display_name(grok) == "xai/grok-4"
        providers = dict(get_available_providers())
        assert providers[Provider.OPENAI_COMPATIBLE] == (
            "Registered (OpenAI-compatible doors)"
        )

    def test_require_reasoning_filters_registered_models(self) -> None:
        register_model(
            "grok-plain", provider=XAI, context_window=8_192, max_output_tokens=1_024
        )
        thinker = register_model(
            "grok-think",
            provider=XAI,
            context_window=8_192,
            max_output_tokens=1_024,
            supports_reasoning=True,
        )
        listed = get_models_for_provider(
            Provider.OPENAI_COMPATIBLE, require_reasoning=True
        )
        assert [m for m, _ in listed] == [thinker]
