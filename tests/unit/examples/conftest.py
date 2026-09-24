import pytest

from tests.support.clock import ManualClock


@pytest.fixture
def manual_clock() -> ManualClock:
    return ManualClock()
