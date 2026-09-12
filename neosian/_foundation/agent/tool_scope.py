"""What one run may call, and how it must answer (DESIGN §3, NC9 #224, #225).

Resolved once at the `Agent._dispatch` funnel and carried on `RunContext`,
the way the usage ledger is: one site covers both drivers, every fallback
rung and the last-resort call, with no per-loop parity to keep.

The scope is also where a schema meets the tools. A tool-enabled agent
cannot be constrained to JSON by the wire while it is still calling
tools, so the schema rides a synthetic final tool instead: one more
declaration on the list, whose arguments *are* the answer. The run ends
when the model calls it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from neosian._foundation.llm.base import ToolCall, ToolDefinition
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    StructuredOutputToolsError,
)
from neosian._foundation.shared.types import (
    ResponseFormat,
    ToolChoice,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import get_tool_metadata

if TYPE_CHECKING:
    from pydantic import BaseModel

    from neosian._foundation.agent.base import Agent

FINAL_TOOL = ToolName("final_response")

_FINAL_TOOL_DESCRIPTION = (
    "Give the final answer. Call this tool, and only this one, once the "
    "other tools have told you everything you need: its arguments are the "
    "answer's fields."
)
_UNKNOWN_TOOL = "This agent has no tool named {name!r}. It registers: {known}"
_NOT_A_TOOL = "tools= takes tool names or @Tool functions; {value!r} is neither"
_FORCED_WITHOUT_TOOLS = (
    "tool_choice forces a call ({mode}), but this run sends no tools"
)
_FORCED_OUTSIDE_SCOPE = (
    "tool_choice names {name!r}, which this run does not send. It sends: {sent}"
)
_FINAL_TOOL_TAKEN = (
    "A registered tool is named {name!r}, which is the name a schema's "
    "final tool takes. Rename the tool, or drop response_format."
)


@dataclass(frozen=True, slots=True)
class ToolScope:
    """One run's tool list, its choice, and the schema riding either.

    Empty is the ordinary shape for a tool-free agent; `definitions` is
    what the caller narrowed the registry to, in the order they gave.
    """

    definitions: tuple[ToolDefinition, ...] = ()
    choice: ToolChoice | None = None
    response_format: ResponseFormat | None = None
    # The synthetic answer-carrier; None when the wire carries the schema
    # itself (no tools in play) or when there is no schema at all.
    final_tool: ToolDefinition | None = None

    @property
    def wire_tools(self) -> list[ToolDefinition] | None:
        """The declarations this call sends; None when it sends none."""
        tools = list(self.definitions)
        if self.final_tool is not None:
            tools.append(self.final_tool)
        return tools or None

    @property
    def wire_format(self) -> ResponseFormat | None:
        """The schema the wire constrains on; None when the final tool has it."""
        return None if self.final_tool is not None else self.response_format

    @property
    def wire_choice(self) -> ToolChoice | None:
        """The choice this call sends; a choice without tools is a 400."""
        return self.choice if self.wire_tools else None

    def last_resort(self) -> ToolScope:
        """The shape of the call made once the tool budget is spent.

        No real tools, so the model must answer. A schema still has to
        land somewhere: its final tool stays, forced, rather than the run
        returning prose where the caller asked for a type.
        """
        if self.final_tool is None:
            return replace(self, definitions=(), choice=None)
        return replace(
            self, definitions=(), choice=ToolChoice.tool(self.final_tool.name)
        )

    def final_call(self, calls: list[ToolCall]) -> ToolCall | None:
        """The answer-carrying call among this turn's, if the model made it."""
        if self.final_tool is None:
            return None
        return next((call for call in calls if call.name == FINAL_TOOL), None)

    def parse(self, call: ToolCall) -> BaseModel:
        """The final call's arguments, validated against the schema."""
        from neosian._foundation.shared.schema import validate_json

        assert self.response_format is not None
        parsed: BaseModel = validate_json(
            self.response_format.schema, json.dumps(call.arguments)
        )
        return parsed


def _name_of(value: str | ToolFunction) -> ToolName:
    """A tool's registry name, from the name itself or its function."""
    if isinstance(value, str):
        return ToolName(value)
    metadata = get_tool_metadata(value)
    if metadata is None:
        raise ConfigurationError(_NOT_A_TOOL.format(value=value))
    return metadata.name


def _subset(agent: Agent, names: list[ToolName]) -> tuple[ToolDefinition, ...]:
    """The named definitions, in the caller's order.

    The registry is the only name authority: a name it does not hold is
    the caller's error, never a silently smaller tool list.
    """
    by_name = {definition.name: definition for definition in agent._tool_definitions}
    chosen: list[ToolDefinition] = []
    for name in names:
        definition = by_name.get(name)
        if definition is None:
            raise ConfigurationError(
                _UNKNOWN_TOOL.format(
                    name=name, known=", ".join(sorted(by_name)) or "no tools"
                )
            )
        chosen.append(definition)
    return tuple(chosen)


def _final_tool(response_format: ResponseFormat) -> ToolDefinition:
    """The synthetic declaration a schema rides when tools are in play."""
    from neosian._foundation.shared.schema import get_json_schema

    return ToolDefinition(
        name=FINAL_TOOL,
        description=_FINAL_TOOL_DESCRIPTION,
        parameters=get_json_schema(response_format.schema),
        strict=response_format.strict,
    )


def resolve_scope(
    agent: Agent,
    *,
    tools: list[str | ToolFunction] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
) -> ToolScope:
    """Resolve one run's tool scope, refusing what no wire could honor.

    `tools=None` is every registered tool — the shape every run had before
    this existed; `tools=[]` is a deliberate tool-free call.
    """
    definitions = (
        tuple(agent._tool_definitions)
        if tools is None
        else _subset(agent, [_name_of(value) for value in tools])
    )
    if tool_choice is not None:
        if tool_choice.forces_a_call and not definitions:
            raise ConfigurationError(
                _FORCED_WITHOUT_TOOLS.format(mode=tool_choice.mode)
            )
        if tool_choice.mode == "tool" and tool_choice.name not in {
            definition.name for definition in definitions
        }:
            raise ConfigurationError(
                _FORCED_OUTSIDE_SCOPE.format(
                    name=tool_choice.name,
                    sent=", ".join(d.name for d in definitions) or "no tools",
                )
            )
    scope = ToolScope(
        definitions=definitions,
        choice=tool_choice,
        response_format=response_format,
    )
    if response_format is None or not definitions:
        # No tools in play: the wire carries the schema, as it always has.
        return scope
    if tool_choice is not None and tool_choice.mode == "tool":
        # Forced to some other tool, the model can never emit the answer.
        raise StructuredOutputToolsError(tool_choice.name or "")
    if tool_choice is not None and tool_choice.mode == "none":
        # Nothing will be called, so the schema stays on the wire and the
        # tools ride along as description only.
        return scope
    if any(definition.name == FINAL_TOOL for definition in definitions):
        raise ConfigurationError(_FINAL_TOOL_TAKEN.format(name=FINAL_TOOL))
    return replace(scope, final_tool=_final_tool(response_format))
