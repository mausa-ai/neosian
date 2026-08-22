"""`revert_memory` — undo from version rows (NP, ledger #101).

One rule collapses every case: no live document before row N → delete;
otherwise row N-1's content comes back. Reverts append rows (audit
intact), refuse a stale target (`revert_stale`) and redacted history,
and ride the shared dispatcher's read-only enforcement.
"""

import pytest

from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.revert import revert_memory

_USER = Mount(scope="user:123", mount_path="user")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(_USER, _KB))


async def _create(config: MemoryConfig, path: str, content: str) -> int:
    result = await dispatch(config, "create", {"path": path, "content": content})
    assert result.success and result.receipt is not None
    return result.receipt.version


class TestInverseTable:
    async def test_undo_edit_restores_prior_content(self, config: MemoryConfig) -> None:
        await _create(config, "/user/prefs", "likes tea")
        edited = await _create(config, "/user/prefs", "likes coffee")
        result = await revert_memory(
            config, "/user/prefs", version=edited, actor="host:undo"
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "likes tea"
        # Append-only: the revert is v3, never a rewrite of history.
        assert document.version == 3
        assert document.actor == "host:undo"

    async def test_undo_fresh_create_removes_the_document(
        self, config: MemoryConfig
    ) -> None:
        version = await _create(config, "/user/tmp", "scratch")
        result = await revert_memory(config, "/user/tmp", version=version)
        assert result.success
        assert await config.store.read(_USER.scope, "tmp") is None
        rows = await config.store.versions(_USER.scope, "tmp")
        assert [row.action for row in rows] == ["deleted", "created"]

    async def test_undo_delete_restores_the_document(
        self, config: MemoryConfig
    ) -> None:
        await _create(config, "/user/prefs", "keep me")
        deleted = await dispatch(config, "delete", {"path": "/user/prefs"})
        assert deleted.receipt is not None
        result = await revert_memory(
            config, "/user/prefs", version=deleted.receipt.version
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "keep me"

    async def test_undo_recreate_after_delete_removes_again(
        self, config: MemoryConfig
    ) -> None:
        await _create(config, "/user/prefs", "first life")
        await dispatch(config, "delete", {"path": "/user/prefs"})
        recreated = await _create(config, "/user/prefs", "second life")
        result = await revert_memory(config, "/user/prefs", version=recreated)
        assert result.success
        assert "removed" in str(result.data)
        assert await config.store.read(_USER.scope, "prefs") is None

    async def test_success_receipt_is_rebadged_revert(
        self, config: MemoryConfig
    ) -> None:
        edited_from = await _create(config, "/user/prefs", "a")
        version = await _create(config, "/user/prefs", "b")
        result = await revert_memory(config, "/user/prefs", version=version)
        assert result.receipt is not None
        assert result.receipt.command == "revert"
        assert result.receipt.version == 3
        assert f"v{edited_from}" in str(result.data)
        # create's overwrite reminder is noise on an undo — dropped.
        assert result.system_reminder is None


class TestGuards:
    async def test_stale_target_refuses(self, config: MemoryConfig) -> None:
        first = await _create(config, "/user/prefs", "a")
        await _create(config, "/user/prefs", "b")
        result = await revert_memory(config, "/user/prefs", version=first)
        assert not result.success
        assert "[memory_conflict]" in str(result.error)
        assert "revert_stale" in str(result.error)
        # Nothing happened: the live document is untouched.
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None and document.content == "b"

    async def test_no_history_refuses(self, config: MemoryConfig) -> None:
        result = await revert_memory(config, "/user/ghost", version=1)
        assert not result.success
        assert "No history" in str(result.error)

    async def test_mount_root_refuses(self, config: MemoryConfig) -> None:
        result = await revert_memory(config, "/user", version=1)
        assert not result.success
        assert "[memory_path_invalid]" in str(result.error)

    async def test_read_only_mount_refuses(self, config: MemoryConfig) -> None:
        result = await revert_memory(config, "/kb/doc", version=1)
        assert not result.success
        assert "[memory_read_only_mount]" in str(result.error)

    async def test_redacted_history_refuses(self, config: MemoryConfig) -> None:
        version = await _create(config, "/user/secret", "sensitive")
        await config.store.redact(_USER.scope, path="secret")
        result = await revert_memory(config, "/user/secret", version=version)
        assert not result.success
        assert "redacted" in str(result.error)

    async def test_redacted_prior_refuses_restore(self, config: MemoryConfig) -> None:
        """A fresh write over a redacted history cannot be undone into the
        cleared content — restoring "" would fake an undo (C3)."""
        await _create(config, "/user/secret", "sensitive")
        await config.store.redact(_USER.scope, path="secret")
        fresh = await _create(config, "/user/secret", "fresh note")
        result = await revert_memory(config, "/user/secret", version=fresh)
        assert not result.success
        assert "redacted" in str(result.error)
        document = await config.store.read(_USER.scope, "secret")
        assert document is not None and document.content == "fresh note"
