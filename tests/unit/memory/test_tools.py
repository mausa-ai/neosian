"""The `memory` tool: six commands over a real FileStore (DESIGN §8)."""

import logging

import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.index import generate_memory_index
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.tools import (
    NATIVE_MEMORY_TOOL_TYPE,
    create_memory_tool,
)
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import get_tool_definition

_USER = Mount(scope="user:123", mount_path="user", description="user facts")
_PROJECT = Mount(scope="user:123/proj:erp", mount_path="project")
_KB = Mount(scope="tenant:acme/kb:main", mount_path="kb", read_only=True)
_TOOLS_LOGGER = "neosian._foundation.memory.tools"


@pytest.fixture
def config(store: FileStore) -> MemoryConfig:
    return MemoryConfig(store=store, mounts=(_USER, _PROJECT, _KB))


@pytest.fixture
def tool(config: MemoryConfig) -> ToolFunction:
    return create_memory_tool(config, actor="conv-1")


class TestToolDefinition:
    def test_metadata(self, tool: ToolFunction) -> None:
        definition = get_tool_definition(tool)
        assert definition is not None
        assert definition.name == "memory"
        assert definition.description == get_prompt("memory.tool")
        assert len(definition.description) <= 1024  # OpenAI-compat hard cap

    def test_schema(self, tool: ToolFunction) -> None:
        definition = get_tool_definition(tool)
        assert definition is not None
        assert definition.parameters["required"] == ["command"]
        assert definition.parameters["properties"]["command"]["enum"] == [
            "view",
            "create",
            "str_replace",
            "insert",
            "delete",
            "rename",
        ]
        assert definition.parameters["additionalProperties"] is False


class TestView:
    async def test_root_is_the_index(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "espresso")
        result = await tool(command="view", path="/")
        assert result.success
        assert result.data == await generate_memory_index(config.store, config.mounts)

    async def test_path_defaults_to_root(self, tool: ToolFunction) -> None:
        result = await tool(command="view")
        assert result.success
        assert "## /user" in str(result.data)

    async def test_mount_listing(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "espresso")
        await config.store.write(_USER.scope, "notes/api", "rest")
        result = await tool(command="view", path="/user")
        assert result.success
        assert "- /user/prefs" in str(result.data)
        assert "- /user/notes/api" in str(result.data)

    async def test_directory_prefix_listing(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "notes/api", "rest")
        await config.store.write(_USER.scope, "notes-other", "x")
        result = await tool(command="view", path="/user/notes")
        assert result.success
        assert "- /user/notes/api" in str(result.data)
        # The trailing-slash prefix is a directory boundary, not a name prefix.
        assert "notes-other" not in str(result.data)

    async def test_document_with_line_numbers(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "one\ntwo\nthree")
        result = await tool(command="view", path="/user/prefs")
        assert result.success
        assert "1: one" in str(result.data)
        assert "3: three" in str(result.data)

    async def test_redacted_document_is_labeled(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "secret", "hunter2")
        await config.store.redact(_USER.scope, path="secret")
        result = await tool(command="view", path="/user/secret")
        assert result.success
        assert "redacted" in str(result.data)
        assert "hunter2" not in str(result.data)

    async def test_missing_document_fails_with_hint(self, tool: ToolFunction) -> None:
        result = await tool(command="view", path="/user/nope")
        assert not result.success
        assert result.system_reminder is not None


class TestCreate:
    async def test_create(self, tool: ToolFunction, config: MemoryConfig) -> None:
        result = await tool(command="create", path="/user/prefs", content="espresso")
        assert result.success
        assert result.data == "Created /user/prefs (v1)"
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "espresso"
        assert document.actor == "conv-1"

    async def test_overwrite_warns(self, tool: ToolFunction) -> None:
        await tool(command="create", path="/user/prefs", content="v1")
        result = await tool(command="create", path="/user/prefs", content="v2")
        assert result.success
        assert result.system_reminder is not None
        assert "Overwrote" in result.system_reminder

    async def test_mount_root_is_not_a_document(self, tool: ToolFunction) -> None:
        result = await tool(command="create", path="/user", content="x")
        assert not result.success
        assert "[memory_path_invalid]" in str(result.error)

    async def test_missing_content_fails_with_correction(
        self, tool: ToolFunction
    ) -> None:
        result = await tool(command="create", path="/user/prefs")
        assert not result.success
        assert "'content'" in str(result.error)


class TestStrReplace:
    async def test_unique_occurrence(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "likes tea")
        result = await tool(
            command="str_replace",
            path="/user/prefs",
            old_str="tea",
            new_str="espresso",
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "likes espresso"

    async def test_zero_occurrences(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "likes tea")
        result = await tool(
            command="str_replace", path="/user/prefs", old_str="x", new_str="y"
        )
        assert not result.success
        assert "did not appear" in str(result.error)

    async def test_multiple_occurrences_list_lines(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "tea\ncoffee\ntea")
        result = await tool(
            command="str_replace", path="/user/prefs", old_str="tea", new_str="x"
        )
        assert not result.success
        assert "2 times" in str(result.error)
        assert "lines 1, 3" in str(result.error)

    async def test_redacted_document_fails(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "secret", "hunter2")
        await config.store.redact(_USER.scope, path="secret")
        result = await tool(
            command="str_replace", path="/user/secret", old_str="a", new_str="b"
        )
        assert not result.success
        assert "redacted" in str(result.error)

    async def test_missing_document(self, tool: ToolFunction) -> None:
        result = await tool(
            command="str_replace", path="/user/nope", old_str="a", new_str="b"
        )
        assert not result.success
        assert "[memory_document_not_found]" in str(result.error)


class TestInsert:
    async def test_insert_at_top(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "b\nc")
        result = await tool(
            command="insert", path="/user/prefs", insert_line=0, insert_text="a"
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "a\nb\nc"

    async def test_insert_at_end(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "a\nb")
        result = await tool(
            command="insert", path="/user/prefs", insert_line=2, insert_text="c"
        )
        assert result.success
        document = await config.store.read(_USER.scope, "prefs")
        assert document is not None
        assert document.content == "a\nb\nc"

    async def test_out_of_range_names_the_range(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "prefs", "a\nb")
        result = await tool(
            command="insert", path="/user/prefs", insert_line=5, insert_text="x"
        )
        assert not result.success
        assert "0 to 2" in str(result.error)


class TestDelete:
    async def test_delete(self, tool: ToolFunction, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "prefs", "x")
        result = await tool(command="delete", path="/user/prefs")
        assert result.success
        assert await config.store.read(_USER.scope, "prefs") is None

    async def test_delete_missing(self, tool: ToolFunction) -> None:
        result = await tool(command="delete", path="/user/nope")
        assert not result.success


class TestRename:
    async def test_same_mount(self, tool: ToolFunction, config: MemoryConfig) -> None:
        await config.store.write(_USER.scope, "old", "body")
        result = await tool(
            command="rename", old_path="/user/old", new_path="/user/new"
        )
        assert result.success
        assert await config.store.read(_USER.scope, "old") is None
        document = await config.store.read(_USER.scope, "new")
        assert document is not None
        assert document.content == "body"

    async def test_same_mount_occupied_destination(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "a", "1")
        await config.store.write(_USER.scope, "b", "2")
        result = await tool(command="rename", old_path="/user/a", new_path="/user/b")
        assert not result.success
        assert "[memory_conflict]" in str(result.error)

    async def test_cross_mount_moves_content(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "fact", "body")
        result = await tool(
            command="rename", old_path="/user/fact", new_path="/project/fact"
        )
        assert result.success
        assert await config.store.read(_USER.scope, "fact") is None
        document = await config.store.read(_PROJECT.scope, "fact")
        assert document is not None
        assert document.content == "body"

    async def test_cross_mount_occupied_destination(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "fact", "1")
        await config.store.write(_PROJECT.scope, "fact", "2")
        result = await tool(
            command="rename", old_path="/user/fact", new_path="/project/fact"
        )
        assert not result.success
        assert "[memory_conflict]" in str(result.error)
        # The source survives a refused move.
        assert await config.store.read(_USER.scope, "fact") is not None


class TestReadOnlyMount:
    @pytest.mark.parametrize(
        "arguments",
        [
            {"command": "create", "path": "/kb/doc", "content": "x"},
            {
                "command": "str_replace",
                "path": "/kb/doc",
                "old_str": "a",
                "new_str": "b",
            },
            {
                "command": "insert",
                "path": "/kb/doc",
                "insert_line": 0,
                "insert_text": "x",
            },
            {"command": "delete", "path": "/kb/doc"},
            {"command": "rename", "old_path": "/kb/doc", "new_path": "/kb/other"},
        ],
    )
    async def test_writers_fail(
        self, tool: ToolFunction, arguments: dict[str, object]
    ) -> None:
        result = await tool(**arguments)
        assert not result.success
        assert "[memory_read_only_mount]" in str(result.error)
        assert result.system_reminder is not None

    async def test_rename_into_read_only_mount_fails(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_USER.scope, "fact", "x")
        result = await tool(
            command="rename", old_path="/user/fact", new_path="/kb/fact"
        )
        assert not result.success
        assert "[memory_read_only_mount]" in str(result.error)

    async def test_view_still_works(
        self, tool: ToolFunction, config: MemoryConfig
    ) -> None:
        await config.store.write(_KB.scope, "doc", "reference")
        result = await tool(command="view", path="/kb/doc")
        assert result.success


class TestFailureShape:
    async def test_unknown_mount_lists_mounts(self, tool: ToolFunction) -> None:
        result = await tool(command="view", path="/nope/doc")
        assert not result.success
        assert "/user" in str(result.error)
        assert "/project" in str(result.error)

    async def test_store_errors_never_raise(self, tool: ToolFunction) -> None:
        # An invalid store path (reserved segment) surfaces as a fail.
        result = await tool(command="view", path="/user/../etc")
        assert not result.success
        assert "[memory_path_invalid]" in str(result.error)

    async def test_unknown_command_fails_correctively(self, tool: ToolFunction) -> None:
        # Literal constrains the schema only; a model that ignores the
        # enum must be corrected, not misled by a dispatch-branch error.
        result = await tool(command="update", path="/user/prefs", content="x")
        assert not result.success
        assert "Unknown command 'update'" in str(result.error)
        assert result.system_reminder is not None
        for command in ("view", "create", "str_replace", "insert", "delete", "rename"):
            assert command in result.system_reminder
        assert "old_path" not in str(result.error)


class TestNativeFlag:
    """native=True is a transport marker; execution is identical (N4)."""

    def test_default_is_unmarked(self, tool: ToolFunction) -> None:
        definition = get_tool_definition(tool)
        assert definition is not None
        assert definition.native_type is None

    def test_native_marks_transport_only(self, config: MemoryConfig) -> None:
        marked = get_tool_definition(create_memory_tool(config, native=True))
        plain = get_tool_definition(create_memory_tool(config))
        assert marked is not None and plain is not None
        assert marked.native_type == NATIVE_MEMORY_TOOL_TYPE == "memory_20250818"
        assert marked.description == plain.description
        assert marked.parameters == plain.parameters

    def test_factory_calls_are_independent(self, config: MemoryConfig) -> None:
        """Marking one closure's definition never leaks into another's."""
        create_memory_tool(config, native=True)
        plain = get_tool_definition(create_memory_tool(config))
        assert plain is not None
        assert plain.native_type is None

    def test_warns_without_a_memories_mount(
        self, config: MemoryConfig, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_TOOLS_LOGGER):
            create_memory_tool(config, native=True)
        assert any("memories" in r.message for r in caplog.records)

    def test_no_warning_with_a_memories_mount(
        self, store: FileStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        memories = MemoryConfig(
            store=store, mounts=(Mount(scope="user:1", mount_path="memories"),)
        )
        with caplog.at_level(logging.WARNING, logger=_TOOLS_LOGGER):
            create_memory_tool(memories, native=True)
        assert not [r for r in caplog.records if r.name == _TOOLS_LOGGER]

    async def test_native_execution_is_byte_identical(
        self, config: MemoryConfig
    ) -> None:
        """The transport-swap pin: create/view round-trip matches exactly."""
        native_tool = create_memory_tool(config, native=True)
        plain_tool = create_memory_tool(config)
        created = await native_tool(
            command="create", path="/user/prefs", content="espresso"
        )
        assert created.success
        seen_native = await native_tool(command="view", path="/user/prefs")
        seen_plain = await plain_tool(command="view", path="/user/prefs")
        assert seen_native.success
        assert seen_native.data == seen_plain.data
        assert seen_native.system_reminder == seen_plain.system_reminder
