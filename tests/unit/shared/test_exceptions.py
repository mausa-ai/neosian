"""Tests for exceptions."""

import pytest

from neosian._foundation.shared.exceptions import NeosianError


@pytest.mark.unit
class TestNeosianError:
    """Test base exception."""

    def test_can_raise_and_catch(self) -> None:
        """NeosianError should be raisable and catchable."""
        with pytest.raises(NeosianError):
            raise NeosianError("test error")

    def test_inherits_from_exception(self) -> None:
        """NeosianError should inherit from Exception."""
        assert issubclass(NeosianError, Exception)
