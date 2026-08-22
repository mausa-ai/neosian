"""The structured write receipt (NP, ledger #98) — driven through dispatch.

Every successful mutating command attaches a `MemoryWriteReceipt` to its
`ToolResult`; `view` and failures never do; the wire envelope
(`to_json()`) is byte-identical with or without one. The delete receipt
carries the consumed version number (the ABC returns `bool`, `read` is
None after a delete — `versions(limit=1)` is the one place that knows).
"""

import pytest

from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_USER = Mount(scope="user:123", mount_path="user")
_NOTES = Mount(scope="user:123/proj:erp", mount_path="notes")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(_USER, _NOTES, _KB))


class TestMutationsCarryReceipts:
    async def test_create(self, config: MemoryConfig) -> None:
        result = await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        receipt = result.receipt
        assert receipt is not None
        assert receipt.command == "create"
        assert receipt.mount_path == "user"
        assert receipt.path == "/user/a"
        assert receipt.version == 1
        assert receipt.previous_path is None

    async def test_create_overwrite_bumps(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        result = await dispatch(config, "create", {"path": "/user/a", "content": "y"})
        assert result.receipt is not None
        assert result.receipt.version == 2

    async def test_str_replace(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "old text"})
        result = await dispatch(
            config,
            "str_replace",
            {"path": "/user/a", "old_str": "old", "new_str": "new"},
        )
        assert result.receipt is not None
        assert result.receipt.command == "str_replace"
        assert result.receipt.version == 2

    async def test_insert(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "line"})
        result = await dispatch(
            config,
            "insert",
            {"path": "/user/a", "insert_line": 0, "insert_text": "first"},
        )
        assert result.receipt is not None
        assert result.receipt.command == "insert"
        assert result.receipt.version == 2

    async def test_delete_carries_the_consumed_version(
        self, config: MemoryConfig
    ) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        result = await dispatch(config, "delete", {"path": "/user/a"})
        assert result.receipt is not None
        assert result.receipt.command == "delete"
        # v1 was the create; the delete consumed v2 (§8 numbering).
        assert result.receipt.version == 2

    async def test_rename_same_mount(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        result = await dispatch(
            config, "rename", {"old_path": "/user/a", "new_path": "/user/b"}
        )
        receipt = result.receipt
        assert receipt is not None
        assert receipt.command == "rename"
        assert receipt.path == "/user/b"
        assert receipt.previous_path == "/user/a"
        assert receipt.version == 1  # dst continues its own history (§8)

    async def test_rename_cross_mount_same_shape(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        result = await dispatch(
            config, "rename", {"old_path": "/user/a", "new_path": "/notes/a"}
        )
        receipt = result.receipt
        assert receipt is not None
        assert receipt.command == "rename"
        assert receipt.mount_path == "notes"
        assert receipt.path == "/notes/a"
        assert receipt.previous_path == "/user/a"


class TestNonMutationsCarryNone:
    async def test_view_never_has_a_receipt(self, config: MemoryConfig) -> None:
        await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        for path in ("/", "/user", "/user/a"):
            result = await dispatch(config, "view", {"path": path})
            assert result.success and result.receipt is None

    async def test_failures_never_have_a_receipt(self, config: MemoryConfig) -> None:
        read_only = await dispatch(config, "create", {"path": "/kb/a", "content": "x"})
        assert not read_only.success and read_only.receipt is None
        missing = await dispatch(config, "delete", {"path": "/user/ghost"})
        assert not missing.success and missing.receipt is None


class TestWireEnvelopeUnchanged:
    async def test_to_json_never_carries_the_receipt(
        self, config: MemoryConfig
    ) -> None:
        """The receipt is an in-process seam: the envelope the CLI prints
        verbatim (ledger #77) and MCP maps is byte-identical either way."""
        result = await dispatch(config, "create", {"path": "/user/a", "content": "x"})
        assert result.receipt is not None
        assert "receipt" not in result.to_json()
