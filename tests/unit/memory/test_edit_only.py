"""Edit-only mounts — the fixed-document-set policy (DESIGN §8, NP slice B).

Edit-only fixes the *set* of documents, never their contents: creating a
new path, deleting, and renaming (either side) are refused with
`memory_edit_only_mount`; overwrites and in-place edits pass. The whole
table lives here, driven through the dispatcher the way every transport
reaches it.
"""

import pytest

from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_FIXED = Mount(
    scope="user:123/layout:erp",
    mount_path="fixed",
    description="pre-created layout",
    edit_only=True,
)
_USER = Mount(scope="user:123", mount_path="user")


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(_FIXED, _USER))


@pytest.fixture
async def seeded(config: MemoryConfig) -> MemoryConfig:
    """One pre-created document on the edit-only mount (the layout)."""
    await config.store.write(_FIXED.scope, "notes", "line one", actor="operator")
    return config


class TestAllowed:
    async def test_view_document(self, seeded: MemoryConfig) -> None:
        result = await dispatch(seeded, "view", {"path": "/fixed/notes"})
        assert result.success
        assert "line one" in str(result.data)

    async def test_overwrite_create_is_an_edit(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded, "create", {"path": "/fixed/notes", "content": "rewritten"}
        )
        assert result.success
        assert result.system_reminder is not None  # the overwrite reminder stands
        document = await seeded.store.read(_FIXED.scope, "notes")
        assert document is not None
        assert document.content == "rewritten"

    async def test_str_replace(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded,
            "str_replace",
            {"path": "/fixed/notes", "old_str": "one", "new_str": "two"},
        )
        assert result.success

    async def test_insert(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded,
            "insert",
            {"path": "/fixed/notes", "insert_line": 0, "insert_text": "header"},
        )
        assert result.success


class TestRefused:
    async def test_create_new_path(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded, "create", {"path": "/fixed/extra", "content": "x"}
        )
        assert "[memory_edit_only_mount]" in str(result.error)
        assert await seeded.store.read(_FIXED.scope, "extra") is None

    async def test_delete(self, seeded: MemoryConfig) -> None:
        result = await dispatch(seeded, "delete", {"path": "/fixed/notes"})
        assert "[memory_edit_only_mount]" in str(result.error)
        assert await seeded.store.read(_FIXED.scope, "notes") is not None

    async def test_rename_within(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded,
            "rename",
            {"old_path": "/fixed/notes", "new_path": "/fixed/renamed"},
        )
        assert "[memory_edit_only_mount]" in str(result.error)

    async def test_rename_out_of(self, seeded: MemoryConfig) -> None:
        result = await dispatch(
            seeded,
            "rename",
            {"old_path": "/fixed/notes", "new_path": "/user/stolen"},
        )
        assert "[memory_edit_only_mount]" in str(result.error)
        assert await seeded.store.read(_USER.scope, "stolen") is None

    async def test_rename_into(self, seeded: MemoryConfig) -> None:
        await seeded.store.write(_USER.scope, "incoming", "x")
        result = await dispatch(
            seeded,
            "rename",
            {"old_path": "/user/incoming", "new_path": "/fixed/incoming"},
        )
        assert "[memory_edit_only_mount]" in str(result.error)


class TestReadOnlyWins:
    async def test_read_only_reported_before_edit_only(self, store: FileStore) -> None:
        """`writable` runs first: a read-only refusal never mentions edit-only."""
        config = MemoryConfig(
            store=store,
            mounts=(
                Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True),
            ),
        )
        result = await dispatch(config, "delete", {"path": "/kb/doc"})
        assert "[memory_read_only_mount]" in str(result.error)
