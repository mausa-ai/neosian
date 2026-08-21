"""The neosian.otel facade: pinned surface, dependency-free until used
(DESIGN §3, ledger #83)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_otel_all_is_pinned() -> None:
    import neosian.otel

    assert neosian.otel.__all__ == ["otel_hooks"]
    for name in neosian.otel.__all__:
        assert getattr(neosian.otel, name) is not None


@pytest.mark.unit
def test_root_import_does_not_load_otel_facade() -> None:
    """`import neosian` must pull in neither neosian.otel nor the API."""
    code = (
        "import neosian, sys; "
        "assert 'neosian.otel' not in sys.modules; "
        "assert not any(m.startswith('opentelemetry') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_otel_facade_does_not_load_the_api() -> None:
    """The `otel` extra is needed to build hooks, never to import."""
    code = (
        "import neosian.otel, sys; "
        "assert not any(m.startswith('opentelemetry') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
