"""A new shipped provider must reach the binary's configuration table too."""

import re
from pathlib import Path

from neosian._cli.providers import provider_keys


def test_binary_provider_table_matches_the_python_catalog() -> None:
    source = Path("crates/neosian/src/config.rs").read_text()
    rows = re.findall(r'\("([^"\n]+)", "([A-Z_]+)"\)', source)
    assert rows == [(row.name, row.env) for row in provider_keys()]
