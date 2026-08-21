"""Store-truth scoring — every predicate green and red (DESIGN §13.12)."""

import re
from pathlib import Path

import pytest

from neosian._foundation.evaluation.memory_score import check_store
from neosian._foundation.evaluation.memory_types import (
    DocumentExpectation,
    StoreExpectation,
)
from neosian._foundation.evaluation.types import MatchMode, ValueMatcher
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from tests.unit.memory.conftest import ManualClock

_USER = Mount(scope="user:eval", mount_path="user")
_PROJECT = Mount(scope="user:eval/proj:demo", mount_path="project")


@pytest.fixture
def config(tmp_path: Path) -> MemoryConfig:
    store = FileStore(tmp_path / "memory", clock=ManualClock())
    return MemoryConfig(store=store, mounts=(_USER, _PROJECT))


@pytest.mark.unit
class TestDocuments:
    async def test_existing_document_passes(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "prefs", "Drinks espresso.")
        expect = StoreExpectation(
            documents=(
                DocumentExpectation(
                    path="/user/prefs",
                    content=(ValueMatcher(mode=MatchMode.CONTAINS, value="Espresso"),),
                    versions=1,
                    actions=("created",),
                ),
            )
        )
        assert await check_store(config, expect) == ()

    async def test_missing_document_names_the_path(self, config: MemoryConfig) -> None:
        expect = StoreExpectation(
            documents=(DocumentExpectation(path="/project/stack"),)
        )
        assert await check_store(config, expect) == (
            "store: no document at /project/stack",
        )

    async def test_content_miss_uses_response_semantics(
        self, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "Drinks tea.")
        expect = StoreExpectation(
            documents=(
                DocumentExpectation(
                    path="/user/prefs",
                    content=(ValueMatcher(mode=MatchMode.CONTAINS, value="espresso"),),
                ),
            )
        )
        (failure,) = await check_store(config, expect)
        assert "document '/user/prefs'" in failure
        assert "expected to contain 'espresso'" in failure

    async def test_version_count_miss_names_the_actions(
        self, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "v1")
        expect = StoreExpectation(
            documents=(DocumentExpectation(path="/user/prefs", versions=2),)
        )
        (failure,) = await check_store(config, expect)
        assert "expected 2 version rows, got 1 (actions: created)" in failure

    async def test_actions_pin_oldest_first(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "prefs", "v1")
        await config.store.write(_USER.scope, "prefs", "v2")
        good = StoreExpectation(
            documents=(
                DocumentExpectation(
                    path="/user/prefs", actions=("created", "modified")
                ),
            )
        )
        assert await check_store(config, good) == ()
        bad = StoreExpectation(
            documents=(
                DocumentExpectation(path="/user/prefs", actions=("created", "created")),
            )
        )
        (failure,) = await check_store(config, bad)
        assert "expected actions created, created, got created, modified" in failure


@pytest.mark.unit
class TestCounts:
    async def test_exact_count_passes(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "a", "x")
        await config.store.write(_USER.scope, "b", "y")
        expect = StoreExpectation(counts={"/user": 2, "/project": 0})
        assert await check_store(config, expect) == ()

    async def test_count_miss_names_the_offenders(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "health", "x")
        await config.store.write(_USER.scope, "health-2", "y")
        (failure,) = await check_store(config, StoreExpectation(counts={"/user": 1}))
        assert re.search(
            r"expected 1 document\(s\) under /user, got 2 \(health, health-2\)",
            failure,
        )


@pytest.mark.unit
class TestAbsentAndForbidden:
    async def test_absent_passes_and_fails(self, config: MemoryConfig) -> None:
        expect = StoreExpectation(absent=("/user/gone",))
        assert await check_store(config, expect) == ()
        await config.store.write(_USER.scope, "gone", "still here")
        (failure,) = await check_store(config, expect)
        assert failure == "store: expected no document at /user/gone, found v1"

    async def test_forbidden_scans_every_mount(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "clean", "nothing secret")
        await config.store.write(_PROJECT.scope, "leak", "token sk-eval-000 here")
        expect = StoreExpectation(forbidden=("sk-eval-000",))
        (failure,) = await check_store(config, expect)
        assert failure == ("store: forbidden text 'sk-eval-000' found in /project/leak")
        await config.store.delete(_PROJECT.scope, "leak")
        assert await check_store(config, expect) == ()
