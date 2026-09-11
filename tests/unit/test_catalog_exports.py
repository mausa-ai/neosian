"""The neosian.catalog facade: aliases of the enum's door rows, pinned (§31)."""

import pytest


@pytest.mark.unit
def test_catalog_all_is_pinned() -> None:
    import neosian.catalog
    from neosian import Model

    assert neosian.catalog.__all__ == ["GEMINI_3_7_FLASH", "GROK_4_6"]
    assert neosian.catalog.GROK_4_6 is Model.GROK_4_6
    assert neosian.catalog.GEMINI_3_7_FLASH is Model.GEMINI_3_7_FLASH
