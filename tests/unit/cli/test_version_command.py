"""`neosian version` says what the install carries (NX, DESIGN §29.10).

The banner is where the shell can speak: the `v<version>` token CI and the
installer grep stays on its own line, the doors are named beside it, and
the one extra is reported by presence.
"""

import pytest

from neosian import __version__
from neosian._cli.main import version


@pytest.mark.unit
def test_the_banner_names_the_version_and_the_doors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    version()
    out = capsys.readouterr().out
    assert f"v{__version__}" in out
    assert "MCP" in out and "state process" in out
    # The dev environment carries the driver (the dev group does).
    assert "psycopg (PostgresStore): installed" in out


@pytest.mark.unit
def test_the_banner_names_the_extra_when_the_driver_is_absent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("neosian._cli.version.find_spec", lambda _name: None)
    version()
    out = capsys.readouterr().out
    assert "psycopg (PostgresStore): missing" in out
    assert 'uv add "neosian[postgres]" adds it' in out
