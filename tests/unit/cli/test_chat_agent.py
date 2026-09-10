"""The resident agent is data (DESIGN §30.2): the chat pack validated at
import like the memory pack, the docs tool wired from `tools.yaml`, the
config on the home and this directory's layout."""

from pathlib import Path

import pytest

from neosian._cli.chat_agent import (
    RESIDENT_NAME,
    create_docs_tool,
    render_system,
    resident_config,
    with_chat_tools,
)
from neosian._foundation.shared.docs_assets import load_page
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import AgentConfig, Model
from neosian._foundation.tools.base import get_tool_definition


class TestThePack:
    def test_the_chat_pack_is_loaded_at_import(self) -> None:
        system = get_prompt("chat.system")
        assert "{{home}}" in system and "{{mounts}}" in system
        assert "neosian docs" in system and "/project" in system

    def test_the_docs_tool_is_wired_from_the_pack(self) -> None:
        definition = get_tool_definition(create_docs_tool())
        assert definition is not None
        assert definition.name == "docs"
        assert definition.description == get_prompt("tools.docs")
        assert definition.parameters["properties"]["topic"]["description"] == (
            get_prompt_params("tools.docs_params")["topic"]
        )


class TestTheDocsTool:
    async def test_a_known_topic_returns_the_page_verbatim(self) -> None:
        result = await create_docs_tool()(topic="topology")
        page = load_page("topology")
        assert page is not None
        assert result.success and result.data == page.body
        assert result.system_reminder == page.title

    async def test_an_unknown_topic_fails_naming_the_topics(self) -> None:
        result = await create_docs_tool()(topic="nope")
        assert not result.success
        assert "nope" in str(result.error)
        assert "topology" in str(result.system_reminder)


class TestTheConfig:
    def test_the_system_names_the_home_and_the_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "demo proj"
        project.mkdir()
        monkeypatch.chdir(project)
        system = render_system()
        assert str(tmp_path / "home") in system
        assert "- /project = " in system and "/proj:demo-proj" in system
        assert "{{" not in system

    def test_the_resident_agent_on_the_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config = resident_config(Model.FAKE)
        assert config.model is Model.FAKE and not config.enable_todo
        assert [t.__name__ for t in config.tools] == ["docs"]
        assert config.memory is not None
        assert [m.mount_path for m in config.memory.mounts] == ["user", "project"]
        assert RESIDENT_NAME == "neosian"

    def test_an_agent_file_gets_chats_tools_added(self) -> None:
        base = AgentConfig(system_prompt="x", model=Model.FAKE)
        derived = with_chat_tools(base)
        assert [t.__name__ for t in derived.tools] == ["docs"]
        assert base.tools == []  # never mutated
