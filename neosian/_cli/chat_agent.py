"""The resident agent (DESIGN §30.2): an agent that knows neosian, all as
data — the `chat.system` asset rendered with the home and this
directory's layout, a `docs` tool over the shipped pages (tools-only
recall: no page body in the prefix), and the memory tool on the project
layout, which brings the skills tools with it.

No `from __future__ import annotations`: @Tool resolves the signature's
annotations at decoration time.
"""

from pathlib import Path

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import home, project_mounts
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.shared.constants import BuiltinTools
from neosian._foundation.shared.docs_assets import list_topics, load_page
from neosian._foundation.shared.prompt_assets import (
    get_prompt,
    get_prompt_params,
    render,
)
from neosian._foundation.shared.types import AgentConfig, AnyModel, ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

RESIDENT_NAME = "neosian"


def create_docs_tool() -> ToolFunction:
    """`docs(topic)` — one shipped page, verbatim, on demand."""

    @Tool(
        name=BuiltinTools.Docs.NAME,
        description=get_prompt("tools.docs"),
        params=get_prompt_params("tools.docs_params"),
    )
    async def docs(topic: str) -> ToolResult[str]:
        """Read one page of neosian's documentation."""
        page = load_page(topic)
        if page is None:
            known = ", ".join(entry.topic for entry in list_topics())
            return ToolResult.fail(
                f"unknown topic {topic!r}", system_reminder=f"Known topics: {known}"
            )
        return ToolResult.ok(page.body, system_reminder=f"{page.title}")

    return docs


def render_system(cwd: Path | None = None) -> str:
    """The asset with the home and this directory's two mounts spelled in."""
    mounts = "\n".join(f"- /{m.mount_path} = {m.scope}" for m in project_mounts(cwd))
    return render(get_prompt("chat.system"), home=str(home()), mounts=mounts)


def resident_config(model: AnyModel, *, cwd: Path | None = None) -> AgentConfig:
    """The resident agent on the home: the rendered prompt, the docs tool,
    memory on this directory's layout (skills ride with it)."""
    return AgentConfig(
        system_prompt=render_system(cwd),
        tools=[create_docs_tool()],
        model=model,
        enable_todo=False,
        memory=MemoryConfig(store=FileStore(home()), mounts=project_mounts(cwd)),
    )


def with_chat_tools(config: AgentConfig) -> AgentConfig:
    """An agent file's config with chat's tools added — the playground
    path, plus the resident agent's doors."""
    from dataclasses import replace

    return replace(config, tools=[*config.tools, create_docs_tool()])
