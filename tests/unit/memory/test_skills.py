"""Skills as documents in a mount (DESIGN §24): the store source, the two
tools, and curation by mount flag through the one memory tool."""

import pytest

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.memory.revert import revert_memory
from neosian._foundation.memory.skills import (
    SkillEntry,
    create_skill_tools,
    list_skills,
    load_skill,
)
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.prompt_assets import get_prompt
from neosian._foundation.shared.types import Skill, SkillName
from neosian._foundation.tools.base import get_tool_definition

USER = "user:demo"
PROJECT = "user:demo/proj:app"
RELEASE = "---\ndescription: Release this project\n---\n1. bump\n2. tag"
DIRECTORY = [Skill(SkillName("lint"), "Lint the tree", "run make lint")]


def _config(
    store: FileStore, *, read_only: bool = False, edit_only: bool = False
) -> MemoryConfig:
    project = Mount(PROJECT, "project", read_only=read_only, edit_only=edit_only)
    return MemoryConfig(store=store, mounts=(Mount(USER, "user"), project))


@pytest.mark.unit
class TestTheSource:
    async def test_lists_mounts_in_order_then_the_directory(
        self, store: FileStore
    ) -> None:
        await store.write(PROJECT, "skills/release", RELEASE)
        await store.write(USER, "skills/review", "---\ndescription: Review\n---\nlook")
        entries = await list_skills(_config(store), DIRECTORY)
        assert [(e.name, e.path, e.version) for e in entries] == [
            ("review", "/user/skills/review", 1),
            ("release", "/project/skills/release", 1),
            ("lint", None, None),
        ]
        assert entries[1].skill is not None
        assert entries[1].skill.content == "1. bump\n2. tag"

    async def test_only_direct_children_and_never_redacted(
        self, store: FileStore
    ) -> None:
        await store.write(PROJECT, "skills/release", RELEASE)
        await store.write(PROJECT, "skills/release/notes", "supporting file")
        await store.write(PROJECT, "skills/erased", RELEASE)
        await store.redact(PROJECT, path="skills/erased")
        await store.write(PROJECT, "skillsets", "not under the prefix")
        entries = await list_skills(_config(store), ())
        assert [e.name for e in entries] == ["release"]

    async def test_an_invalid_document_is_listed_with_its_fix(
        self, store: FileStore
    ) -> None:
        await store.write(PROJECT, "skills/broken", "no frontmatter at all")
        (entry,) = await list_skills(_config(store), ())
        assert entry == SkillEntry(
            "broken", "/project/skills/broken", version=1, error=entry.error
        )
        assert entry.error is not None and "frontmatter" in entry.error

    async def test_load_by_name_takes_the_first_mount(self, store: FileStore) -> None:
        await store.write(USER, "skills/release", "---\ndescription: mine\n---\nu")
        await store.write(PROJECT, "skills/release", RELEASE)
        entry = await load_skill(_config(store), DIRECTORY, "release")
        assert entry is not None and entry.path == "/user/skills/release"
        by_path = await load_skill(_config(store), (), "/project/skills/release")
        assert by_path is not None and by_path.version == 1
        assert by_path.skill is not None and by_path.skill.description.startswith(
            "Release"
        )

    async def test_load_misses(self, store: FileStore) -> None:
        config = _config(store)
        await store.write(PROJECT, "notes/release", RELEASE)
        assert await load_skill(config, DIRECTORY, "nope") is None
        assert await load_skill(config, (), "/project/notes/release") is None
        assert await load_skill(config, (), "a/b") is None
        assert await load_skill(None, (), "/project/skills/x") is None
        directory = await load_skill(None, DIRECTORY, "lint")
        assert directory is not None and directory.skill is DIRECTORY[0]


@pytest.mark.unit
class TestTheTools:
    def test_definitions(self) -> None:
        listing, loading = create_skill_tools(DIRECTORY, None)
        listed, loaded = get_tool_definition(listing), get_tool_definition(loading)
        assert listed is not None and listed.name == "list_skills"
        assert loaded is not None and loaded.name == "load_skill"
        assert "name" in loaded.parameters["properties"]

    async def test_list_rows_and_the_guide_only_when_writable(
        self, store: FileStore
    ) -> None:
        await store.write(PROJECT, "skills/release", RELEASE)
        await store.write(PROJECT, "skills/broken", "nope")
        listing, _ = create_skill_tools(DIRECTORY, _config(store))
        result = await listing()
        assert result.success and result.data is not None
        assert result.data[0] == {
            "name": "broken",
            "error": result.data[0]["error"],
            "path": "/project/skills/broken",
            "version": 1,
        }
        assert result.data[1] == {
            "name": "release",
            "description": "Release this project",
            "path": "/project/skills/release",
            "version": 1,
        }
        assert result.data[2] == {"name": "lint", "description": "Lint the tree"}
        assert result.system_reminder == get_prompt("tools.skill_guide")

        read_only = MemoryConfig(
            store=store, mounts=(Mount(scope=PROJECT, mount_path="p", read_only=True),)
        )
        listing, _ = create_skill_tools((), read_only)
        assert (await listing()).system_reminder is None
        listing, _ = create_skill_tools(DIRECTORY, None)
        directory_only = await listing()
        assert directory_only.data == [{"name": "lint", "description": "Lint the tree"}]
        assert directory_only.system_reminder is None

    async def test_load_names_the_document(self, store: FileStore) -> None:
        await store.write(PROJECT, "skills/release", RELEASE)
        _, loading = create_skill_tools(DIRECTORY, _config(store))
        result = await loading(name="release")
        assert result.success and result.data == "1. bump\n2. tag"
        assert result.system_reminder == "/project/skills/release (version 1)"
        assert (await loading(name="lint")).system_reminder is None

    async def test_load_failures_are_corrective(self, store: FileStore) -> None:
        await store.write(PROJECT, "skills/broken", "nope")
        _, loading = create_skill_tools(DIRECTORY, _config(store))
        missing = await loading(name="nope")
        assert not missing.success and "nope" in str(missing.error)
        assert missing.system_reminder == "Available skills: broken, lint"
        broken = await loading(name="broken")
        assert not broken.success and "frontmatter" in str(broken.error)
        assert broken.system_reminder is not None
        assert "/project/skills/broken (version 1)" in broken.system_reminder
        stranger = await loading(name="/nowhere/skills/x")
        assert not stranger.success and "no mount named" in str(stranger.error)


@pytest.mark.unit
class TestCurationByFlag:
    """Writing needs no tool of its own: the memory tool under the mount
    flag is the whole write side, receipts and versions included."""

    async def test_read_only_refuses(self, store: FileStore) -> None:
        memory = create_memory_tool(_config(store, read_only=True))
        result = await memory(
            command="create", path="/project/skills/release", content=RELEASE
        )
        assert not result.success
        assert "[memory_read_only_mount]" in str(result.error)

    async def test_edit_only_fixes_the_set(self, store: FileStore) -> None:
        await store.write(PROJECT, "skills/release", RELEASE)
        memory = create_memory_tool(_config(store, edit_only=True))
        new = await memory(command="create", path="/project/skills/x", content=RELEASE)
        assert "[memory_edit_only_mount]" in str(new.error)
        revised = await memory(
            command="str_replace",
            path="/project/skills/release",
            old_str="2. tag",
            new_str="2. tag\n3. push",
        )
        assert revised.success

    async def test_read_write_versions_receipts_and_revert(
        self, store: FileStore
    ) -> None:
        config = _config(store)
        memory = create_memory_tool(config, actor="conv:t1#1")
        _, loading = create_skill_tools((), config)
        created = await memory(
            command="create", path="/project/skills/release", content=RELEASE
        )
        assert created.receipt is not None and created.receipt.version == 1
        revised = await memory(
            command="str_replace",
            path="/project/skills/release",
            old_str="2. tag",
            new_str="2. tag\n3. push --follow-tags",
        )
        assert revised.receipt is not None and revised.receipt.version == 2
        loaded = await loading(name="release")
        assert loaded.data is not None and "follow-tags" in loaded.data
        assert loaded.system_reminder == "/project/skills/release (version 2)"

        rows = await store.versions(PROJECT, "skills/release")
        assert [(r.version, r.action, r.actor) for r in rows] == [
            (2, "modified", "conv:t1#1"),
            (1, "created", "conv:t1#1"),
        ]
        reverted = await revert_memory(
            config, "/project/skills/release", version=2, actor="cli:local"
        )
        assert reverted.success
        restored = await loading(name="release")
        assert restored.data == "1. bump\n2. tag"
        assert restored.system_reminder == "/project/skills/release (version 3)"
