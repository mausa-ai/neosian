"""The neosian.server facade: pinned surface, extra-free until used
(DESIGN §1, §18). `RemoteStore` is the wire's client half and rides the
core install — httpx is a core dependency, so it never needs the extra.
"""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_server_all_is_pinned() -> None:
    import neosian.server

    assert neosian.server.__all__ == ["build_app"]
    for name in neosian.server.__all__:
        assert getattr(neosian.server, name) is not None


@pytest.mark.unit
def test_an_unknown_attribute_still_raises() -> None:
    import neosian.server

    with pytest.raises(AttributeError, match="serve_forever"):
        _ = neosian.server.serve_forever


@pytest.mark.unit
def test_root_import_does_not_load_the_server_extra() -> None:
    """`import neosian` pulls in neither the facade nor starlette/uvicorn —
    RemoteStore lives on the root package and must stay extra-free."""
    code = (
        "import neosian, sys; "
        "assert neosian.RemoteStore is not None; "
        "assert 'neosian.server' not in sys.modules; "
        "assert 'starlette' not in sys.modules; "
        "assert 'uvicorn' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_the_facade_import_does_not_load_the_extra() -> None:
    """The `server` extra is needed to serve, never to import the facade."""
    code = (
        "import neosian.server, sys; "
        "assert 'starlette' not in sys.modules; "
        "assert 'uvicorn' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_the_entry_point_module_does_not_load_the_extra() -> None:
    """`--help`, grammar errors and the missing-token refusal must answer
    without starlette or uvicorn installed (§14.1's exit-2 tier)."""
    code = (
        "import neosian.server.serve, sys; "
        "assert 'starlette' not in sys.modules; "
        "assert 'uvicorn' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_remote_store_does_not_load_the_extra() -> None:
    """The client half speaks httpx only — no serving stack, ever."""
    code = (
        "from neosian._foundation.server.remote import RemoteStore; "
        "import sys; "
        "assert 'starlette' not in sys.modules; "
        "assert 'uvicorn' not in sys.modules; "
        "assert 'neosian._foundation.server.sdk' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
