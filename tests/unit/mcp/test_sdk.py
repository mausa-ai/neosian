"""The lazy SDK loader: a helpful ImportError without the extra."""

import sys

import pytest

from neosian._foundation.mcp.sdk import load_sdk


class TestLoadSdk:
    def test_loads_the_installed_sdk(self) -> None:
        sdk = load_sdk()
        assert sdk.server_class.__name__ == "Server"
        assert sdk.tool.__name__ == "Tool"

    def test_missing_sdk_names_the_extra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # All three keys: `from mcp.server import Server` short-circuits on
        # an already-cached `mcp.server` before it ever consults `mcp`.
        monkeypatch.setitem(sys.modules, "mcp", None)
        monkeypatch.setitem(sys.modules, "mcp.server", None)
        monkeypatch.setitem(sys.modules, "mcp.types", None)
        with pytest.raises(ImportError, match=r"neosian\[mcp\]"):
            load_sdk()
