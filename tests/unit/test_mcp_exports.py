"""The neosian.mcp facade: pinned surface, SDK-free until used (DESIGN §1, §8)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_mcp_all_is_pinned() -> None:
    import neosian.mcp

    assert neosian.mcp.__all__ == ["McpServer", "create_memory_server"]
    for name in neosian.mcp.__all__:
        assert getattr(neosian.mcp, name) is not None


@pytest.mark.unit
def test_root_import_does_not_load_mcp_facade() -> None:
    """`import neosian` must pull in neither neosian.mcp nor the SDK."""
    code = (
        "import neosian, sys; "
        "assert 'neosian.mcp' not in sys.modules; "
        "assert 'mcp' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_mcp_facade_does_not_load_the_sdk() -> None:
    """The SDK is needed to serve, never to import the facade."""
    code = "import neosian.mcp, sys; assert 'mcp' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_entry_point_module_does_not_load_the_sdk() -> None:
    """`--help` and grammar errors must work without the SDK loaded."""
    code = "import neosian.mcp.serve, sys; assert 'mcp' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_client_side_does_not_load_the_sdk() -> None:
    """`McpServer` and the bridge import without the SDK — it
    loads when a server is entered, never before (DESIGN §25)."""
    code = (
        "import neosian._foundation.mcp.client, neosian._foundation.mcp.bridge, "
        "sys; assert 'mcp' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.unit
def test_install_module_does_not_load_the_sdk() -> None:
    """`mcp install` works without the SDK — it never serves (its import
    is function-local in serve.main, so the serve pin does not cover it)."""
    code = (
        "import neosian._foundation.mcp.install, sys; assert 'mcp' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
