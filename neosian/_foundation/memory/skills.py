"""Skills as documents in a mount (DESIGN §24, ledger #154–#157).

A skill is the document `skills/<name>` under any mount: the mount's
scope owns it, the mount's flag says who may write it (`ro` immutable,
`eo` a fixed set the agent revises, `rw` agent-written), and the version
rows, receipts, redaction, revert and audit are the store's. The two
tools here read the store live per call — a skill written this session
is loadable this session — and `skill_dir` files ride along as an
immutable second source. Writing needs no tool of its own: `memory
create /project/skills/<name>` is the write. Deeper paths
(`skills/<name>/<file>`) are a skill's supporting documents, never
skills.

No `from __future__ import annotations`: @Tool resolves the signature's
hints at decoration time.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from neosian._foundation.memory.dispatch import corrective
from neosian._foundation.memory.mounts import MemoryConfig, Mount, resolve
from neosian._foundation.shared.constants import BuiltinTools, ErrorMessages
from neosian._foundation.shared.exceptions import MemoryStoreError, SkillLoadError
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.skill import skill_from_document
from neosian._foundation.shared.types import Skill, ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

SKILLS_PREFIX: Final = "skills/"


@dataclass(frozen=True, slots=True)
class SkillEntry:
    """One skill by address: parsed, or the error its author must fix.
    `path`/`version` are the store's; a directory skill has neither."""

    name: str
    path: str | None = None
    skill: Skill | None = None
    version: int | None = None
    error: str | None = None


async def _read(config: MemoryConfig, mount: Mount, name: str) -> SkillEntry | None:
    if "/" in name:
        return None
    document = await config.store.read(mount.scope, SKILLS_PREFIX + name)
    if document is None or document.redacted:
        return None
    path = f"/{mount.mount_path}/{SKILLS_PREFIX}{name}"
    try:
        skill = skill_from_document(name, document.content, path)
    except SkillLoadError as exc:
        return SkillEntry(name, path, version=document.version, error=exc.message)
    return SkillEntry(name, path, skill, document.version)


async def list_skills(
    config: MemoryConfig | None, directory: Sequence[Skill]
) -> list[SkillEntry]:
    """Every skill: the mounts in config order, then the directory."""
    entries: list[SkillEntry] = []
    if config is not None:
        for mount in config.mounts:
            listed = await config.store.list_documents(
                mount.scope, prefix=SKILLS_PREFIX
            )
            for listing in listed:
                entry = await _read(config, mount, listing.path[len(SKILLS_PREFIX) :])
                if entry is not None:
                    entries.append(entry)
    entries.extend(SkillEntry(skill.name, skill=skill) for skill in directory)
    return entries


async def load_skill(
    config: MemoryConfig | None, directory: Sequence[Skill], name: str
) -> SkillEntry | None:
    """One skill by name (the first in listing order) or by its
    `/mount/skills/name` path; `None` when nothing is there."""
    if name.startswith("/"):
        if config is None:
            return None
        mount, rest = resolve(config, name)
        if not rest.startswith(SKILLS_PREFIX):
            return None
        return await _read(config, mount, rest[len(SKILLS_PREFIX) :])
    if config is not None:
        for mount in config.mounts:
            entry = await _read(config, mount, name)
            if entry is not None:
                return entry
    return next(
        (
            SkillEntry(skill.name, skill=skill)
            for skill in directory
            if skill.name == name
        ),
        None,
    )


def _row(entry: SkillEntry) -> dict[str, Any]:
    row: dict[str, Any] = {"name": entry.name}
    if entry.skill is not None:
        row["description"] = entry.skill.description
    else:
        row["error"] = entry.error
    if entry.path is not None:
        row.update(path=entry.path, version=entry.version)
    return row


def create_skill_tools(
    directory: Sequence[Skill], memory: MemoryConfig | None
) -> tuple[ToolFunction, ToolFunction]:
    """The `list_skills` and `load_skill` tools over `directory` skills and
    the `skills/` documents of `memory`'s mounts."""
    writable = memory is not None and any(not m.read_only for m in memory.mounts)

    @Tool(
        name=BuiltinTools.Skill.LIST_NAME,
        description=get_prompt("tools.skill_list"),
    )
    async def list_skills_tool() -> ToolResult[list[dict[str, Any]]]:
        """List the available skills with their descriptions; a skill in a
        mount shows its path and version."""
        rows = [_row(entry) for entry in await list_skills(memory, directory)]
        guide = get_prompt("tools.skill_guide") if writable else None
        return ToolResult.ok(rows, system_reminder=guide)

    @Tool(
        name=BuiltinTools.Skill.LOAD_NAME,
        description=get_prompt("tools.skill_load"),
        params=get_prompt_params("tools.skill_load_params"),
    )
    async def load_skill_tool(name: str) -> ToolResult[str]:
        """Load a skill's instructions."""
        try:
            entry = await load_skill(memory, directory, name)
        except MemoryStoreError as exc:
            return corrective(exc)
        if entry is None:
            names = ", ".join(e.name for e in await list_skills(memory, directory))
            return ToolResult.fail(
                ErrorMessages.SKILL_NOT_FOUND.format(name=name),
                system_reminder=f"Available skills: {names}",
            )
        if entry.skill is None:
            return ToolResult.fail(
                str(entry.error),
                system_reminder=f"Fix {entry.path} (version {entry.version}) "
                "with the memory tool's str_replace.",
            )
        located = (
            None if entry.path is None else f"{entry.path} (version {entry.version})"
        )
        return ToolResult.ok(entry.skill.content, system_reminder=located)

    return list_skills_tool, load_skill_tool
