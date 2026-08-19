"""Mounts and virtual-path resolution (DESIGN §8 tool layer, C7)."""

import dataclasses

import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import (
    MemoryConfig,
    Mount,
    resolve,
    writable,
)
from neosian._foundation.shared.exceptions import (
    MemoryPathInvalidError,
    MemoryReadOnlyMountError,
    MemoryScopeInvalidError,
)


class TestMount:
    def test_valid_mount(self) -> None:
        mount = Mount(scope="user:123", mount_path="user", description="facts")
        assert not mount.read_only

    @pytest.mark.parametrize("scope", ["", "User:1", "user", "user:", "u:1//p:2"])
    def test_invalid_scope_rejected(self, scope: str) -> None:
        with pytest.raises(MemoryScopeInvalidError):
            Mount(scope=scope, mount_path="user")

    @pytest.mark.parametrize("mount_path", ["", "a/b", "/user", ".", ".."])
    def test_mount_path_must_be_one_segment(self, mount_path: str) -> None:
        with pytest.raises(MemoryPathInvalidError):
            Mount(scope="user:123", mount_path=mount_path)

    def test_frozen(self) -> None:
        mount = Mount(scope="user:123", mount_path="user")
        with pytest.raises(dataclasses.FrozenInstanceError):
            mount.read_only = True  # type: ignore[misc]


class TestMemoryConfig:
    def test_normalizes_mounts_to_tuple(self, store: FileStore) -> None:
        config = MemoryConfig(
            store=store,
            mounts=[Mount(scope="user:123", mount_path="user")],  # type: ignore[arg-type]
        )
        assert isinstance(config.mounts, tuple)

    def test_requires_at_least_one_mount(self, store: FileStore) -> None:
        with pytest.raises(ValueError, match="at least one mount"):
            MemoryConfig(store=store, mounts=())

    def test_mount_paths_must_be_unique(self, store: FileStore) -> None:
        with pytest.raises(ValueError, match="unique"):
            MemoryConfig(
                store=store,
                mounts=(
                    Mount(scope="user:1", mount_path="user"),
                    Mount(scope="user:2", mount_path="user"),
                ),
            )


class TestResolve:
    @pytest.fixture
    def config(self, store: FileStore) -> MemoryConfig:
        return MemoryConfig(
            store=store,
            mounts=(
                Mount(scope="user:123", mount_path="user"),
                Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True),
            ),
        )

    @pytest.mark.parametrize(
        ("path", "expected_mount", "expected_doc"),
        [
            ("/user/prefs", "user", "prefs"),
            ("user/prefs", "user", "prefs"),
            ("/user/prefs/", "user", "prefs"),
            ("/user/a/b/c", "user", "a/b/c"),
            ("/user", "user", ""),
            ("user/", "user", ""),
            ("/kb/doc", "kb", "doc"),
        ],
    )
    def test_resolution(
        self,
        config: MemoryConfig,
        path: str,
        expected_mount: str,
        expected_doc: str,
    ) -> None:
        mount, doc_path = resolve(config, path)
        assert mount.mount_path == expected_mount
        assert doc_path == expected_doc

    @pytest.mark.parametrize("path", ["", "/", "//"])
    def test_root_is_not_a_mount(self, config: MemoryConfig, path: str) -> None:
        with pytest.raises(MemoryPathInvalidError, match="root"):
            resolve(config, path)

    def test_unknown_mount_lists_available(self, config: MemoryConfig) -> None:
        with pytest.raises(MemoryPathInvalidError, match="/user, /kb"):
            resolve(config, "/nope/doc")


class TestWritable:
    def test_read_only_mount_raises(self) -> None:
        mount = Mount(scope="user:123", mount_path="kb", read_only=True)
        with pytest.raises(MemoryReadOnlyMountError) as excinfo:
            writable(mount)
        assert excinfo.value.code == "memory_read_only_mount"
        assert excinfo.value.mount_path == "kb"

    def test_writable_mount_passes(self) -> None:
        writable(Mount(scope="user:123", mount_path="user"))
