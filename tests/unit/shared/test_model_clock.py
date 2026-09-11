"""The catalog clock (DESIGN §31, ROADMAP §NW): every shipped row carries its
provider's lifecycle as data, and this test fails `make test` inside 30 days
of a retirement or a card's end — the fingerprint's sibling. It reads the
wall clock on purpose: the alarm is about today, not about a run.

A fired alarm is a row fact whose fate is the user's ruling in the next
session (remove the row, move the date the provider moved, or record the
ruling here); never a silent edit.
"""

from datetime import date, timedelta

import pytest

from neosian import Model, Provider

_WARNING = timedelta(days=30)

# Rows whose alarm has fired and been ruled on: member -> the ruling, dated.
# A row leaves in the next release after its provider's date (ledger #210),
# so an entry here is short-lived by construction.
_RULED: dict[Model, str] = {}


def _clocks() -> list[tuple[Model, str, date]]:
    return [
        (model, field, when)
        for model in Model
        if model.provider is not Provider.FAKE
        for field, when in (
            ("retires", model.spec.retires),
            ("card_until", model.spec.card_until),
        )
        if when is not None
    ]


@pytest.mark.unit
class TestCatalogClock:
    def test_the_clock_is_data_on_the_rows_it_names(self) -> None:
        assert Model.CLAUDE_HAIKU_4_5.spec.retires == date(2026, 10, 15)
        assert Model.GEMINI_3_8_FLASH.spec.card_until == date(2026, 12, 31)
        assert Model.GPT_5_6_SOL.spec.retires is None
        assert Model.FAKE.spec.retires is None and Model.FAKE.spec.card_until is None

    @pytest.mark.parametrize(
        ("model", "field", "when"),
        _clocks(),
        ids=[f"{m.value}:{f}" for m, f, _ in _clocks()],
    )
    def test_no_shipped_row_is_inside_thirty_days_without_a_ruling(
        self, model: Model, field: str, when: date
    ) -> None:
        if model in _RULED:
            return
        assert date.today() + _WARNING < when, (
            f"Model.{model.name} ({model.value!r}) {field} on {when.isoformat()}, "
            f"inside 30 days: a ruling is owed (ROADMAP §NW, the freshness "
            f"ritual) — remove the row, move the date, or record the ruling "
            f"in _RULED."
        )
