"""Skills served over MCP (DESIGN §24.3): the two read-only tools beside
memory, every skill an MCP prompt, and the done-when's second agent
reading a skill through a read-only mount on the same store."""

from pathlib import Path

import pytest
from mcp.client import Client

from neosian._foundation.mcp.server import create_memory_server
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.prompt_assets import get_prompt

RELEASE = "---\ndescription: Release this project\n---\n1. bump\n2. tag"


class TestTools:
    async def test_the_state_set_lists_skills_after_memory(
        self, config: MemoryConfig, store: FileStore
    ) -> None:
        server = await create_memory_server(config, conversations=store)
        async with Client(server) as client:
            result = await client.list_tools()
        names = [tool.name for tool in result.tools]
        assert names == ["memory", "list_skills", "load_skill", "recall_turn"]
        listing = result.tools[1]
        assert listing.description == get_prompt("tools.skill_list")
        assert listing.annotations is not None
        assert listing.annotations.read_only_hint is True
        assert listing.annotations.destructive_hint is False

    async def test_a_skill_written_through_memory_is_loadable(
        self, config: MemoryConfig
    ) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            created = await client.call_tool(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/skills/release",
                    "content": RELEASE,
                },
            )
            assert created.is_error is False
            listed = await client.call_tool("list_skills", {})
            loaded = await client.call_tool("load_skill", {"name": "release"})
            missing = await client.call_tool("load_skill", {"name": "nope"})
        assert listed.is_error is False
        assert "/memories/skills/release" in listed.content[0].text  # type: ignore[union-attr]
        assert loaded.is_error is False
        assert loaded.content[0].text == "1. bump\n2. tag"  # type: ignore[union-attr]
        assert loaded.content[1].text == "/memories/skills/release (version 1)"  # type: ignore[union-attr]
        assert missing.is_error is True


class TestPrompts:
    async def test_every_skill_is_a_prompt_listed_live(
        self, config: MemoryConfig, store: FileStore
    ) -> None:
        server = await create_memory_server(config)
        async with Client(server) as client:
            assert (await client.list_prompts()).prompts == []
            await store.write("user:demo", "skills/release", RELEASE)
            await store.write("user:demo", "skills/broken", "no frontmatter")
            (prompt,) = (await client.list_prompts()).prompts
            assert prompt.name == "release"
            assert prompt.description == "Release this project"
            got = await client.get_prompt("release")
            with pytest.raises(Exception, match="'nope' not found"):
                await client.get_prompt("nope")
        assert got.description == "Release this project"
        (message,) = got.messages
        assert message.role == "user"
        assert message.content.text == "1. bump\n2. tag"  # type: ignore[union-attr]


class TestASecondAgentReadsOnly:
    async def test_read_only_mount_on_the_same_store(self, tmp_path: Path) -> None:
        """The done-when: agent A writes a skill under its rw mount; agent
        B's server mounts the same scope read-only, reads it, and cannot
        write it — the mount flag is the whole curation policy."""
        writer = MemoryConfig(
            store=FileStore(tmp_path / "home"),
            mounts=(Mount("user:demo", "memories"),),
        )
        reader = MemoryConfig(
            store=FileStore(tmp_path / "home"),
            mounts=(Mount("user:demo", "ref", read_only=True),),
        )
        async with Client(await create_memory_server(writer)) as a:
            await a.call_tool(
                "memory",
                {
                    "command": "create",
                    "path": "/memories/skills/release",
                    "content": RELEASE,
                },
            )
        async with Client(await create_memory_server(reader)) as b:
            loaded = await b.call_tool("load_skill", {"name": "release"})
            prompts = await b.list_prompts()
            refused = await b.call_tool(
                "memory",
                {"command": "create", "path": "/ref/skills/mine", "content": RELEASE},
            )
        assert loaded.is_error is False
        assert loaded.content[1].text == "/ref/skills/release (version 1)"  # type: ignore[union-attr]
        assert [p.name for p in prompts.prompts] == ["release"]
        assert refused.is_error is True
        assert "memory_read_only_mount" in refused.content[0].text  # type: ignore[union-attr]
