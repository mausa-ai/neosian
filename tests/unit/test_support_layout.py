"""Shared test helpers live in `tests/support/`, never in a conftest
another directory imports (TP-15): a conftest is pytest's plugin, loaded
once per directory, and importing it by module path loads a second copy
beside the one pytest registered."""

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
_CROSS_CONFTEST = re.compile(r"^\s*(?:from|import)\s+tests\.[\w.]*conftest\b", re.M)


def test_no_module_imports_another_directorys_conftest() -> None:
    offenders = [
        str(path.relative_to(_TESTS))
        for path in sorted(_TESTS.rglob("*.py"))
        if _CROSS_CONFTEST.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
