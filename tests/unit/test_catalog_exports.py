"""The neosian.catalog facade: the shipped door rows, pinned (DESIGN §19.5)."""

import pytest


@pytest.mark.unit
def test_catalog_all_is_pinned() -> None:
    import neosian.catalog

    assert neosian.catalog.__all__ == ["GEMINI_3_7_FLASH", "GROK_4_6"]
    assert neosian.catalog.GROK_4_6.value == "grok-4.6"
    assert neosian.catalog.GEMINI_3_7_FLASH.value == "gemini-3.7-flash"
