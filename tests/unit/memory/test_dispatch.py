"""The shared command ladder at the dict seam (DESIGN §8, ledger #50).

`test_tools.py` exercises the ladder through the function tool's typed
closure; this suite drives `dispatch` the way the MCP transport does —
raw mapping in, `command` untrusted — and pins the seam-only behavior:
the non-string command guard, unknown-key tolerance, the `file_text`
alias, and every corrective hint.
"""

from pathlib import Path

import pytest

from neosian._foundation.memory.dispatch import _HINTS, dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount

_USER = Mount(scope="user:123", mount_path="user", description="user facts")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)
_FIXED = Mount(scope="user:123/layout:erp", mount_path="fixed", edit_only=True)


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(_USER, _KB, _FIXED))


class TestCommandGuard:
    @pytest.mark.parametrize("command", [None, 3, ["view"], "update"])
    async def test_untrusted_command_fails_correctively(
        self, config: MemoryConfig, command: object
    ) -> None:
        result = await dispatch(config, command, {})
        assert not result.success
        assert f"Unknown command {command!r}" in str(result.error)
        assert result.system_reminder is not None
        for name in ("view", "create", "str_replace", "insert", "delete", "rename"):
            assert name in result.system_reminder


class TestArguments:
    async def test_view_path_defaults_to_root(self, config: MemoryConfig) -> None:
        result = await dispatch(config, "view", {})
        assert result.success
        assert "/user" in str(result.data)

    async def test_unknown_keys_are_ignored(self, config: MemoryConfig) -> None:
        # additionalProperties: false is schema steering, not a guarantee.
        result = await dispatch(
            config, "view", {"path": "/", "flavor": "vanilla", "depth": 3}
        )
        assert result.success

    async def test_file_text_is_creates_alias(self, config: MemoryConfig) -> None:
        result = await dispatch(
            config, "create", {"path": "/user/prefs", "file_text": "from native"}
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "from native"

    async def test_content_wins_over_file_text(self, config: MemoryConfig) -> None:
        result = await dispatch(
            config,
            "create",
            {"path": "/user/prefs", "content": "ours", "file_text": "native"},
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "ours"

    @pytest.mark.parametrize(
        ("command", "arguments", "missing"),
        [
            ("create", {"content": "x"}, "path"),
            ("create", {"path": "/user/a"}, "content"),
            ("str_replace", {"path": "/user/a", "new_str": "b"}, "old_str"),
            ("str_replace", {"path": "/user/a", "old_str": "a"}, "new_str"),
            ("insert", {"path": "/user/a", "insert_text": "x"}, "insert_line"),
            ("insert", {"path": "/user/a", "insert_line": 0}, "insert_text"),
            ("delete", {}, "path"),
            ("rename", {"new_path": "/user/b"}, "old_path"),
            ("rename", {"old_path": "/user/a"}, "new_path"),
        ],
    )
    async def test_missing_argument_names_the_parameter(
        self,
        config: MemoryConfig,
        command: str,
        arguments: dict[str, object],
        missing: str,
    ) -> None:
        result = await dispatch(config, command, arguments)
        assert not result.success
        assert f"The {command!r} command requires the {missing!r} parameter" in str(
            result.error
        )

    async def test_actor_reaches_the_version_row(self, config: MemoryConfig) -> None:
        await dispatch(
            config, "create", {"path": "/user/a", "content": "x"}, actor="mcp"
        )
        versions = await config.store.versions(_USER.scope, "a")
        assert versions[0].actor == "mcp"


class TestHints:
    """Every `_HINTS` code is reachable through the dispatcher."""

    async def test_document_not_found(self, config: MemoryConfig) -> None:
        result = await dispatch(
            config,
            "str_replace",
            {"path": "/user/absent", "old_str": "a", "new_str": "b"},
        )
        assert "[memory_document_not_found]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_document_not_found"]

    async def test_path_invalid(self, config: MemoryConfig) -> None:
        result = await dispatch(config, "view", {"path": "/user/../etc"})
        assert "[memory_path_invalid]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_path_invalid"]

    async def test_read_only_mount(self, config: MemoryConfig) -> None:
        result = await dispatch(config, "create", {"path": "/kb/doc", "content": "x"})
        assert "[memory_read_only_mount]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_read_only_mount"]

    async def test_edit_only_mount(self, config: MemoryConfig) -> None:
        result = await dispatch(
            config, "create", {"path": "/fixed/doc", "content": "x"}
        )
        assert "[memory_edit_only_mount]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_edit_only_mount"]

    async def test_conflict(self, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "a", "1")
        await config.store.write(_USER.scope, "b", "2")
        result = await dispatch(
            config, "rename", {"old_path": "/user/a", "new_path": "/user/b"}
        )
        assert "[memory_conflict]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_conflict"]

    async def test_format_unsupported(
        self, config: MemoryConfig, tmp_path: Path
    ) -> None:
        await config.store.write(_USER.scope, "future", "x")
        raw = tmp_path / "memory" / "user%3A123" / "documents" / "future.md"
        raw.write_text(
            raw.read_text().replace("neosian_format: 1", "neosian_format: 999"),
            encoding="utf-8",
            newline="",
        )
        result = await dispatch(config, "view", {"path": "/user/future"})
        assert "[memory_format_unsupported]" in str(result.error)
        assert result.system_reminder == _HINTS["memory_format_unsupported"]
