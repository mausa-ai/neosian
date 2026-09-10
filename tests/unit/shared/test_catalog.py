"""The shipped door rows (DESIGN §19.5, §31): `Model` members with a door,
sealed by the fingerprint, reaching their key through the door."""

import dataclasses
import os
from unittest.mock import patch

import pytest

from neosian import (
    PRICES_FINGERPRINT,
    ConfigurationError,
    MissingAPIKeyError,
    Model,
    ModelPricing,
    Provider,
    lookup_model,
    register_model,
)
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.catalog import GEMINI, XAI
from neosian._foundation.shared.models import _MODEL_SPECS, _prices_fingerprint
from neosian._foundation.shared.registry import provider_label, registered_models

DOOR_ROWS = {
    Model.GROK_4_6: XAI,
    Model.GEMINI_3_8_FLASH: GEMINI,
    Model.GEMINI_3_7_FLASH: GEMINI,
}


@pytest.mark.unit
class TestCatalog:
    def test_the_door_rows_are_the_enums_and_register_nothing(self) -> None:
        assert {m for m in Model if m.door is not None} == set(DOOR_ROWS)
        for row, door in DOOR_ROWS.items():
            assert row.door is door
            assert lookup_model(row.value) is row
        assert registered_models() == ()

    def test_rows_are_priced_door_rows(self) -> None:
        for row, door in DOOR_ROWS.items():
            assert row.provider is Provider.OPENAI_COMPATIBLE
            assert row.pricing is not None
            assert provider_label(row) == door.name
        assert provider_label(Model.FAKE) == "fake"

    def test_a_shipped_id_cannot_be_registered(self) -> None:
        with pytest.raises(ConfigurationError, match="Model.GROK_4_6"):
            register_model(
                "grok-4.6", provider=XAI, context_window=1, max_output_tokens=1
            )

    def test_catalog_prices_are_sealed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cheaper = dataclasses.replace(
            _MODEL_SPECS["grok-4.6"],
            pricing=ModelPricing(input_per_mtok=1, output_per_mtok=1),
        )
        monkeypatch.setitem(_MODEL_SPECS, "grok-4.6", cheaper)
        assert _prices_fingerprint() != PRICES_FINGERPRINT

    def test_a_row_reads_its_key_through_its_door(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(MissingAPIKeyError, match="XAI_API_KEY"),
        ):
            ProviderRouter().create_client_for(Model.GROK_4_6)


@pytest.mark.unit
def test_the_facade_aliases_the_enum() -> None:
    import neosian.catalog

    assert neosian.catalog.GROK_4_6 is Model.GROK_4_6
    assert neosian.catalog.GEMINI_3_7_FLASH is Model.GEMINI_3_7_FLASH
