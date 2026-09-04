"""Neosian never parses meaning from a scope (DESIGN §8).

The pin: `scope_segments` is called nowhere outside memory/scope.py, and
`scope_directory` (the storage encoding) only by scope.py and file_layout.py.
Interpreting scopes — containment, inheritance, routing on type — is
host territory; these greps keep it that way mechanically.
"""

from pathlib import Path

import neosian

_PACKAGE_ROOT = Path(neosian.__file__).parent

_ALLOWED_CALLERS = {
    "scope_segments": {Path("_foundation/memory/scope.py")},
    "scope_directory": {
        Path("_foundation/memory/scope.py"),
        Path("_foundation/memory/file_layout.py"),
    },
}


def _callers(token: str) -> set[Path]:
    found = set()
    for source in _PACKAGE_ROOT.rglob("*.py"):
        if token in source.read_text(encoding="utf-8"):
            found.add(source.relative_to(_PACKAGE_ROOT))
    return found


class TestScopeDecompositionStaysInScopePy:
    def test_scope_segments_has_no_callers_outside_scope_py(self) -> None:
        assert _callers("scope_segments") == _ALLOWED_CALLERS["scope_segments"]

    def test_scope_directory_is_called_only_by_scope_and_layout(self) -> None:
        assert _callers("scope_directory") == _ALLOWED_CALLERS["scope_directory"]
