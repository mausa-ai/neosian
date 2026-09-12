"""The eval harness reaches into no Agent private (DESIGN §13.11).

The v1 harness rewrote `agent._tools` and did `agent._tool_definitions[i]`
surgery; NE retired both for construction-time tools plus hook-based
observation. The pin: the three private tool tokens are touched only by
the agent/tools modules that own them — `evaluation/` appears nowhere.
Tokens are dot-prefixed so names like `_max_parallel_tools` cannot
false-positive.
"""

from pathlib import Path

import neosian

_PACKAGE_ROOT = Path(neosian.__file__).parent

_ALLOWED_CALLERS = {
    "._tools": {
        Path("_foundation/agent/base.py"),  # the registry's owner
        Path("_foundation/agent/tool_exec.py"),  # dispatch reads it
    },
    "._tool_definitions": {
        Path("_foundation/agent/base.py"),  # built at construction
        # Since NC9 the drivers read `ctx.scope`, not the registry: the
        # per-call subset is resolved once, here (#224).
        Path("_foundation/agent/tool_scope.py"),  # narrowed to the run's
    },
    "._tool_metadata": {
        Path("_foundation/tools/base.py"),  # the attribute's owner
    },
    # The acquire seam (DESIGN §3): Agent passes it to RunContext, the
    # session caches through it, and the memory runner's reflection step
    # borrows it — the one sanctioned reach, honoring client_factory.
    "._create_client": {
        Path("_foundation/agent/base.py"),  # the owner (+ guardrails)
        Path("_foundation/agent/session.py"),  # the session cache
        Path("_foundation/evaluation/memory_runner.py"),  # reflect/maintain acquire
    },
}


def _callers(token: str) -> set[Path]:
    found = set()
    for source in _PACKAGE_ROOT.rglob("*.py"):
        if token in source.read_text(encoding="utf-8"):
            found.add(source.relative_to(_PACKAGE_ROOT))
    return found


class TestAgentPrivatesStayAgentOwned:
    def test_the_tool_registry_is_reached_only_by_its_owners(self) -> None:
        assert _callers("._tools") == _ALLOWED_CALLERS["._tools"]

    def test_tool_definitions_are_reached_only_by_the_agent_loop(self) -> None:
        assert _callers("._tool_definitions") == _ALLOWED_CALLERS["._tool_definitions"]

    def test_tool_metadata_stays_inside_its_owning_module(self) -> None:
        assert _callers("._tool_metadata") == _ALLOWED_CALLERS["._tool_metadata"]

    def test_the_acquire_seam_is_reached_only_where_sanctioned(self) -> None:
        assert _callers("._create_client") == _ALLOWED_CALLERS["._create_client"]
