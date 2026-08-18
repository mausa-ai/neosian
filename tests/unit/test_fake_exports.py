"""The neosian.fake facade: pinned surface, lazily loaded (DESIGN §1)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_fake_all_is_pinned() -> None:
    import neosian.fake

    assert neosian.fake.__all__ == [
        "FakeCall",
        "FakeClient",
        "FakeScript",
        "FakeScriptExhaustedError",
        "FakeTurn",
        "StreamShape",
    ]
    for name in neosian.fake.__all__:
        assert getattr(neosian.fake, name) is not None


@pytest.mark.unit
def test_root_import_does_not_load_fake_facade() -> None:
    """`import neosian` must not pull in neosian.fake (lazy, excludable)."""
    code = "import neosian, sys; assert 'neosian.fake' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
