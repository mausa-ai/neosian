"""Conversation's reach into AgentSession's privates is deliberate and
bounded (DESIGN §9.5.14, ledger #33).

The pin: outside `agent/` itself, `_rebind` and `_get_or_create_client`
are touched only by `conversation/core.py` — the compaction-boundary
rebind and the `acquire` lease. Any new caller (or a rename on the
session side) breaks here loudly instead of at runtime.
"""

from pathlib import Path

import neosian

_PACKAGE_ROOT = Path(neosian.__file__).parent

_ALLOWED_CALLERS = {
    "_rebind": {
        Path("_foundation/agent/session.py"),  # the definition
        Path("_foundation/conversation/core.py"),  # the boundary rebind
    },
    "_get_or_create_client": {
        Path("_foundation/agent/session.py"),  # the definition
        Path("_foundation/agent/context.py"),  # docstring: the acquire seam
        Path("_foundation/agent/base.py"),  # the session run path
        Path("_foundation/conversation/core.py"),  # distillation's lease
    },
}


def _callers(token: str) -> set[Path]:
    found = set()
    for source in _PACKAGE_ROOT.rglob("*.py"):
        if token in source.read_text(encoding="utf-8"):
            found.add(source.relative_to(_PACKAGE_ROOT))
    return found


class TestSessionSeamStaysBounded:
    def test_rebind_is_reached_only_by_the_boundary(self) -> None:
        assert _callers("_rebind") == _ALLOWED_CALLERS["_rebind"]

    def test_client_lease_is_reached_only_by_sanctioned_sites(self) -> None:
        assert (
            _callers("_get_or_create_client")
            == _ALLOWED_CALLERS["_get_or_create_client"]
        )
