"""The shipped catalog rows (DESIGN §19.5): registered at import, sealed."""

import dataclasses
import os
from unittest.mock import patch

import pytest

from neosian import (
    PRICES_FINGERPRINT,
    ConfigurationError,
    MissingAPIKeyError,
    ModelPricing,
    Provider,
    register_model,
)
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.catalog import CATALOG, GROK_4_6, XAI
from neosian._foundation.shared.models import CATALOG_SPECS, _prices_fingerprint
from neosian._foundation.shared.registry import lookup_model, provider_label


@pytest.mark.unit
class TestCatalog:
    def test_rows_are_registered_at_import(self) -> None:
        for row in CATALOG:
            assert lookup_model(row.value) is row
        assert set(CATALOG_SPECS) == {row.value for row in CATALOG}

    def test_rows_are_priced_door_rows(self) -> None:
        for row in CATALOG:
            assert row.provider is Provider.OPENAI_COMPATIBLE
            assert row.pricing is not None
            assert provider_label(row) == row.door.name

    def test_a_catalog_id_is_write_once(self) -> None:
        with pytest.raises(ConfigurationError, match="already registered"):
            register_model(
                "grok-4.6", provider=XAI, context_window=1, max_output_tokens=1
            )

    def test_catalog_prices_are_sealed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cheaper = dataclasses.replace(
            CATALOG_SPECS["grok-4.6"],
            pricing=ModelPricing(input_per_mtok=1, output_per_mtok=1),
        )
        monkeypatch.setitem(CATALOG_SPECS, "grok-4.6", cheaper)
        assert _prices_fingerprint() != PRICES_FINGERPRINT

    def test_a_row_reads_its_key_through_its_door(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(MissingAPIKeyError, match="XAI_API_KEY"),
        ):
            ProviderRouter().create_client_for(GROK_4_6)
