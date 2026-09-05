"""Tool construction for the eval agent (DESIGN §13.6).

Tools are built *before* the agent exists and never touched after — the
v1 rewrite of the agent's private tool registry is gone. By default every user tool becomes a
stub returning a canned success (or the turn's `tool_results` payload);
names on the execute allowlist pass the original function through.
Variant description overrides ride the same construction: the harness
owns the wrappers it makes, so their metadata is legitimately its own.

Library builtins (todo / skill / memory) are registered
by Agent from config fields, not `config.tools` — they always execute
and never appear here.
"""

import dataclasses
import functools
from collections.abc import Mapping, Sequence
from typing import Any

from neosian._foundation.shared.types import ToolFunction, ToolName
from neosian._foundation.tools.base import (
    ToolResult,
    attach_tool_metadata,
    get_tool_metadata,
)

_STUB_PAYLOAD = {"status": "success", "message": "Tool executed successfully"}


class StubResults:
    """Harness-owned per-turn payload table for stubbed tools.

    The runner calls `enter_turn` before each model call, so a stub's
    return value is in place *before* the model sees it — the recorded
    history needs no post-hoc rewriting.
    """

    def __init__(self) -> None:
        self._payloads: Mapping[ToolName, Any] = {}

    def enter_turn(self, tool_results: Mapping[ToolName, Any]) -> None:
        """Set this turn's stub payloads (empty mapping resets)."""
        self._payloads = tool_results

    def payload_for(self, name: ToolName) -> Any:
        """The payload a stub returns: the turn's, or the canned success."""
        if name in self._payloads:
            return self._payloads[name]
        return dict(_STUB_PAYLOAD)


def build_tools(
    tools: Sequence[ToolFunction],
    *,
    execute: frozenset[ToolName],
    descriptions: Mapping[ToolName, str],
    results: StubResults,
) -> tuple[list[ToolFunction], frozenset[ToolName]]:
    """Build the eval agent's tool list from the loaded config's.

    Returns (tools, stubbed names). Originals are never mutated: a name
    on `execute` with no override passes through by identity; everything
    else gets a fresh wrapper carrying a replaced definition.

    Raises:
        ValueError: If `execute` or `descriptions` names an unknown tool
            — a typo here would silently measure the wrong thing.
    """
    new_tools: list[ToolFunction] = []
    stubbed: set[ToolName] = set()
    known: set[ToolName] = set()

    for func in tools:
        metadata = get_tool_metadata(func)
        if metadata is None:
            # Undecorated: pass through — Agent construction raises its
            # own corrective error, which the runner reports per case.
            new_tools.append(func)
            continue
        known.add(metadata.name)
        override = descriptions.get(metadata.name)
        if metadata.name in execute and override is None:
            new_tools.append(func)
            continue
        definition = (
            metadata.definition
            if override is None
            else dataclasses.replace(metadata.definition, description=override)
        )
        if metadata.name in execute:
            wrapper = _passthrough(func)
        else:
            wrapper = _stub(func, metadata.name, results)
            stubbed.add(metadata.name)
        new_tools.append(
            attach_tool_metadata(wrapper, definition, arguments=metadata.arguments)
        )

    unknown_execute = sorted(str(n) for n in execute - known)
    if unknown_execute:
        raise ValueError(
            f"execute_tools names unknown tool(s): {unknown_execute} — builtin "
            "tools (todo/skill/memory) always execute and need "
            "not be listed"
        )
    unknown_override = sorted(str(n) for n in set(descriptions) - known)
    if unknown_override:
        raise ValueError(f"variant overrides unknown tool(s): {unknown_override}")
    return new_tools, frozenset(stubbed)


def _stub(original: ToolFunction, name: ToolName, results: StubResults) -> ToolFunction:
    @functools.wraps(original)
    async def stub(**_kwargs: Any) -> ToolResult[Any]:
        return ToolResult.ok(results.payload_for(name))

    return stub


def _passthrough(original: ToolFunction) -> ToolFunction:
    @functools.wraps(original)
    async def passthrough(**kwargs: Any) -> ToolResult[Any]:
        return await original(**kwargs)

    return passthrough
