"""The index at scale: tiering and the budget (DESIGN §8, NG).

Under the budget the rendering is byte-identical to the unbudgeted form;
over it, the least recently updated documents fold into per-directory
count lines, and the floor collapses a mount to its total. The NG
done-when: the index holds its budget at 500 documents.
"""

from pathlib import Path

from neosian._foundation.memory.commands import view
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import (
    INDEX_BUDGET_CHARS,
    generate_memory_index,
    memory_system_section,
)
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from tests.support.clock import ManualClock

_USER = Mount(scope="user:123", mount_path="user", description="user facts")
_PROJECT = Mount(scope="user:123/proj:erp", mount_path="project")


async def _populate_500(store: FileStore) -> int:
    """Realistic scale: 10 directories x 48 documents + 20 loose docs."""
    written = 0
    for directory in range(10):
        for doc in range(48):
            await store.write(
                _USER.scope, f"area-{directory:02d}/topic-{doc:03d}", "body"
            )
            written += 1
    for doc in range(20):
        await store.write(_USER.scope, f"note-{doc:02d}", "body")
        written += 1
    return written


class TestUnderBudget:
    async def test_rendering_is_byte_identical_to_the_unbudgeted_form(
        self, store: FileStore
    ) -> None:
        await store.write(_USER.scope, "prefs", "espresso")
        await store.write(_USER.scope, "notes/api", "rest")
        index = await generate_memory_index(store, (_USER, _PROJECT))
        assert index == (
            "## /user — user facts\n"
            "- /user/notes/api\n"
            "- /user/prefs\n"
            "\n"
            "## /project\n"
            "(empty)"
        )
        huge = await generate_memory_index(store, (_USER, _PROJECT), budget_chars=10**9)
        assert huge == index


class TestFolding:
    async def test_oldest_documents_fold_into_directory_counts(
        self, store: FileStore
    ) -> None:
        # Written in order, ManualClock ticking: old/* are the oldest.
        await store.write(_USER.scope, "old/one", "a")
        await store.write(_USER.scope, "old/two", "b")
        await store.write(_USER.scope, "loose-old", "c")
        await store.write(_USER.scope, "fresh", "d")
        full = await generate_memory_index(store, (_USER,))
        index = await generate_memory_index(store, (_USER,), budget_chars=len(full) - 1)
        assert index == (
            "## /user — user facts\n"
            "- /user/fresh\n"
            "- /user/loose-old\n"
            "- /user/old/ (2 documents)"
        )
        assert len(index) <= len(full) - 1

    async def test_all_cold_renders_folds_plus_the_loose_remainder(
        self, store: FileStore
    ) -> None:
        await store.write(_USER.scope, "old/one", "a")
        await store.write(_USER.scope, "old/two", "b")
        await store.write(_USER.scope, "loose-old", "c")
        await store.write(_USER.scope, "fresh", "d")
        expected = (
            "## /user — user facts\n" "- /user/old/ (2 documents)\n" "- /user/ (2 more)"
        )
        index = await generate_memory_index(store, (_USER,), budget_chars=len(expected))
        assert index == expected

    async def test_a_single_folded_document_counts_singular(
        self, store: FileStore
    ) -> None:
        await store.write(_USER.scope, "old/only", "a")
        await store.write(_USER.scope, "fresh-one", "b")
        await store.write(_USER.scope, "fresh-two", "c")
        full = await generate_memory_index(store, (_USER,))
        index = await generate_memory_index(store, (_USER,), budget_chars=len(full) - 1)
        assert "- /user/old/ (1 document)" in index

    async def test_a_shrinking_promotion_lands_even_from_over_budget(
        self, store: FileStore
    ) -> None:
        # The doc line is shorter than the loose line it removes, so the
        # promotion shrinks the render below a budget the all-cold form
        # exceeds — the non-monotone case a plain cutoff search misses.
        await store.write(_USER.scope, "old/secret", "hunter2")
        await store.redact(_USER.scope, path="old/secret")
        await store.write(_USER.scope, "fresh", "d")
        full = await generate_memory_index(store, (_USER,))
        index = await generate_memory_index(store, (_USER,), budget_chars=len(full) - 1)
        assert index == (
            "## /user — user facts\n" "- /user/fresh\n" "- /user/old/ (1 document)"
        )

    async def test_the_floor_collapses_a_mount_to_its_total(
        self, store: FileStore
    ) -> None:
        for n in range(6):
            await store.write(_USER.scope, f"dir-{n}/doc-with-a-long-name", "a")
        index = await generate_memory_index(store, (_USER, _PROJECT), budget_chars=1)
        assert "(6 documents — view /user/ to list)" in index
        assert "(empty)" in index
        assert "- /user/" not in index


class TestScale:
    async def test_the_index_holds_its_budget_at_500_documents(
        self, store: FileStore
    ) -> None:
        written = await _populate_500(store)
        assert written == 500
        full = await generate_memory_index(store, (_USER,), budget_chars=10**9)
        assert len(full) > INDEX_BUDGET_CHARS  # the budget is doing real work
        index = await generate_memory_index(store, (_USER,))
        assert len(index) <= INDEX_BUDGET_CHARS

    async def test_every_document_is_named_or_covered_by_its_directory(
        self, store: FileStore
    ) -> None:
        await _populate_500(store)
        index = await generate_memory_index(store, (_USER,))
        for entry in await store.list_documents(_USER.scope):
            direct = f"- /user/{entry.path}" in index
            directory = entry.path.split("/", 1)[0]
            folded = (
                f"- /user/{directory}/ (" in index
                if "/" in entry.path
                else "- /user/ (" in index
            )
            assert direct or folded, entry.path

    async def test_rendering_is_deterministic_across_store_instances(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        await _populate_500(store)
        first = await generate_memory_index(store, (_USER,))
        reopened = FileStore(tmp_path / "memory", clock=ManualClock())
        second = await generate_memory_index(reopened, (_USER,))
        assert first == second

    async def test_view_root_serves_the_same_budgeted_rendering(
        self, store: FileStore
    ) -> None:
        await _populate_500(store)
        config = MemoryConfig(store=store, mounts=(_USER,))
        result = await view(config, "/")
        assert result.success
        assert result.data == await generate_memory_index(store, (_USER,))


class TestSystemSection:
    async def test_the_section_passes_the_budget_through(
        self, store: FileStore
    ) -> None:
        for n in range(6):
            await store.write(_USER.scope, f"dir-{n}/doc", "a")
        config = MemoryConfig(store=store, mounts=(_USER,))
        section = await memory_system_section(config, budget_chars=1)
        assert "(6 documents — view /user/ to list)" in section
