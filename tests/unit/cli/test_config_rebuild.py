"""The --menu and arena config rebuilds carry every field (N2 slice C).

The hand-rolled rebuilds this pins against dropped nine AgentConfig
fields, silently resetting them to defaults — the characterization is
`replace(base, model=...)`, nothing hand-copied.
"""

import dataclasses
from pathlib import Path

import pytest

from neosian import AgentConfig, Model
from neosian._cli.arena import arena_config
from neosian._cli.playground import menu_config
from neosian._foundation.agent.hooks import AgentHooks
from neosian._foundation.llm.fake import FakeClient, FakeScript
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.types import (
    FallbackConfig,
)

# The fields the pre-N2 hand-rolled rebuild silently dropped (the
# blackboard, retired at NB, was the ninth).
_DROPPED = (
    "fallback",
    "max_parallel_tools",
    "max_retries",
    "cache_conversation",
    "skill_dir",
    "client_factory",
    "hooks",
    "context_policy",
)


def _loaded_base(tmp_path: Path) -> AgentConfig:
    """An AgentConfig with every previously dropped field set non-default."""
    fake = FakeClient(FakeScript(turns=()))
    return AgentConfig(
        system_prompt="base",
        model=Model.FAKE,
        enable_todo=False,
        fallback=FallbackConfig(model=Model.FAKE_SMALL, retry_main_after=2),
        max_parallel_tools=3,
        max_retries=7,
        cache_conversation=False,
        skill_dir=tmp_path,
        client_factory=lambda _: fake,
        hooks=AgentHooks(),
        context_policy=None,  # explicitly disabled — must not be re-enabled
        memory=MemoryConfig(
            store=FileStore(tmp_path / "memstore"),
            mounts=(Mount(scope="user:demo", mount_path="memories"),),
        ),
    )


@pytest.mark.unit
class TestMenuConfig:
    def test_is_exactly_replace_with_the_model(self, tmp_path: Path) -> None:
        base = _loaded_base(tmp_path)
        assert menu_config(base, Model.FAKE_REASONING) == dataclasses.replace(
            base, model=Model.FAKE_REASONING
        )

    def test_every_previously_dropped_field_survives(self, tmp_path: Path) -> None:
        base = _loaded_base(tmp_path)
        rebuilt = menu_config(base, Model.FAKE_REASONING)
        assert rebuilt.model is Model.FAKE_REASONING
        for name in _DROPPED:
            assert getattr(rebuilt, name) == getattr(base, name), name
        assert rebuilt.memory is base.memory


@pytest.mark.unit
class TestArenaConfig:
    def test_memory_is_dropped_everything_else_survives(self, tmp_path: Path) -> None:
        base = _loaded_base(tmp_path)
        rebuilt = arena_config(base, Model.FAKE_REASONING)
        assert rebuilt.model is Model.FAKE_REASONING
        assert rebuilt.memory is None  # arena stays memory-less, deliberately
        for name in _DROPPED:
            assert getattr(rebuilt, name) == getattr(base, name), name
