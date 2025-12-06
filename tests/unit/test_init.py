"""Test package initialization."""

import pytest


@pytest.mark.unit
def test_version_exists() -> None:
    """Test that version is defined."""
    from neosian import __version__

    # Just check version exists and is a valid semver-like string
    assert isinstance(__version__, str)
    assert len(__version__.split(".")) >= 2


@pytest.mark.unit
def test_package_imports() -> None:
    """Test that package can be imported."""
    import neosian

    assert neosian is not None
