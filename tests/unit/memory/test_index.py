"""Index generation and the memory system-prompt section (DESIGN §8)."""

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import (
    generate_memory_index,
    memory_system_section,
)
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_USER = Mount(scope="user:123", mount_path="user", description="user facts")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)


class TestGenerateMemoryIndex:
    async def test_empty_mounts_render_as_empty(self, store: FileStore) -> None:
        index = await generate_memory_index(store, (_USER, _KB))
        assert "## /user — user facts" in index
        assert "## /kb (read-only)" in index
        assert index.count("(empty)") == 2

    async def test_documents_listed_by_virtual_path(self, store: FileStore) -> None:
        await store.write(_USER.scope, "prefs", "espresso")
        await store.write(_USER.scope, "notes/api", "rest")
        index = await generate_memory_index(store, (_USER,))
        assert "- /user/notes/api" in index
        assert "- /user/prefs" in index

    async def test_mounts_are_isolated_by_scope(self, store: FileStore) -> None:
        await store.write(_USER.scope, "prefs", "espresso")
        index = await generate_memory_index(store, (_USER, _KB))
        assert index.count("prefs") == 1

    async def test_redacted_documents_are_marked(self, store: FileStore) -> None:
        await store.write(_USER.scope, "secret", "hunter2")
        await store.redact(_USER.scope, path="secret")
        index = await generate_memory_index(store, (_USER,))
        assert "- /user/secret (redacted)" in index


class TestMemorySystemSection:
    async def test_renders_the_pack_with_the_index(self, store: FileStore) -> None:
        await store.write(_USER.scope, "prefs", "espresso")
        config = MemoryConfig(store=store, mounts=(_USER,))
        section = await memory_system_section(config)
        assert "{{index}}" not in section
        assert "- /user/prefs" in section
        assert "## Memory" in section
