"""The console script imports without a terminal menu backend (EC-1).

`simple_term_menu` raises at import where `termios` is missing (Windows);
the three CLI modules that use it import it inside the functions that
draw a menu, so `docs`, `memory`, `mcp` and `serve` boot everywhere.
"""

import subprocess
import sys

import pytest

_CODE = """
import sys
sys.modules["simple_term_menu"] = None  # any import of it raises ImportError
import neosian._cli.main, neosian._cli.models, neosian._cli.arena
"""


@pytest.mark.unit
def test_cli_modules_import_without_simple_term_menu() -> None:
    subprocess.run([sys.executable, "-c", _CODE], check=True, timeout=60)
