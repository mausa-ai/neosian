"""Tool construction — stubs by default, execute allowlist, owned metadata."""

import pytest

from neosian._foundation.evaluation.stubs import StubResults, build_tools
from neosian._foundation.shared.types import ToolFunction, ToolName
from neosian._foundation.tools.base import (
    Tool,
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)

NONE: frozenset[ToolName] = frozenset()


def _make_tools() -> tuple[list[ToolFunction], list[str]]:
    """One real tool with a side-effect log, one plain tool."""
    log: list[str] = []

    @Tool(name="write", description="Write a value", strict=True)
    async def write(value: str) -> ToolResult[str]:
        log.append(value)
        return ToolResult.ok("written")

    @Tool(name="lookup", description="Look something up")
    async def lookup(q: str) -> ToolResult[str]:
        log.append(f"lookup:{q}")
        return ToolResult.ok("found")

    return [write, lookup], log


@pytest.mark.unit
class TestStubbing:
    async def test_default_is_stub_with_canned_success(self) -> None:
        tools, log = _make_tools()
        results = StubResults()
        built, stubbed = build_tools(
            tools, execute=NONE, descriptions={}, results=results
        )
        assert stubbed == frozenset({"write", "lookup"})
        outcome = await built[0](value="x")
        assert outcome.success is True
        assert outcome.data == {
            "status": "success",
            "message": "Tool executed successfully",
        }
        assert log == []  # the real tool never ran

    async def test_turn_payloads_arrive_and_reset(self) -> None:
        tools, _ = _make_tools()
        results = StubResults()
        built, _ = build_tools(tools, execute=NONE, descriptions={}, results=results)
        results.enter_turn({ToolName("write"): {"url": "https://x"}})
        assert (await built[0](value="x")).data == {"url": "https://x"}
        results.enter_turn({})
        reset = (await built[0](value="x")).data
        assert reset is not None and reset["status"] == "success"

    async def test_execute_passes_the_original_through_by_identity(self) -> None:
        tools, log = _make_tools()
        built, stubbed = build_tools(
            tools,
            execute=frozenset({ToolName("write")}),
            descriptions={},
            results=StubResults(),
        )
        assert built[0] is tools[0]
        assert stubbed == frozenset({"lookup"})
        await built[0](value="x")
        assert log == ["x"]

    async def test_execute_with_override_still_runs_the_original(self) -> None:
        tools, log = _make_tools()
        built, _ = build_tools(
            tools,
            execute=frozenset({ToolName("write")}),
            descriptions={ToolName("write"): "Better description"},
            results=StubResults(),
        )
        assert built[0] is not tools[0]
        assert (await built[0](value="y")).data == "written"
        assert log == ["y"]


@pytest.mark.unit
class TestMetadataOwnership:
    def test_wrapper_keeps_schema_and_takes_the_override(self) -> None:
        tools, _ = _make_tools()
        original_definition = get_tool_definition(tools[0])
        assert original_definition is not None
        built, _ = build_tools(
            tools,
            execute=NONE,
            descriptions={ToolName("write"): "Overridden"},
            results=StubResults(),
        )
        definition = get_tool_definition(built[0])
        assert definition is not None
        assert definition.description == "Overridden"
        assert definition.name == original_definition.name
        assert definition.parameters == original_definition.parameters
        assert definition.strict is original_definition.strict

    def test_the_original_is_never_mutated(self) -> None:
        tools, _ = _make_tools()
        build_tools(
            tools,
            execute=NONE,
            descriptions={ToolName("write"): "Overridden"},
            results=StubResults(),
        )
        metadata = get_tool_metadata(tools[0])
        assert metadata is not None
        assert metadata.definition.description == "Write a value"

    def test_undecorated_functions_pass_through_untouched(self) -> None:
        async def bare(**_kwargs: object) -> ToolResult[str]:
            return ToolResult.ok("x")

        built, stubbed = build_tools(
            [bare], execute=NONE, descriptions={}, results=StubResults()
        )
        assert built == [bare]
        assert stubbed == frozenset()


@pytest.mark.unit
class TestUnknownNames:
    def test_unknown_execute_name_names_the_builtin_rule(self) -> None:
        tools, _ = _make_tools()
        with pytest.raises(ValueError, match="always execute"):
            build_tools(
                tools,
                execute=frozenset({ToolName("memory")}),
                descriptions={},
                results=StubResults(),
            )

    def test_unknown_override_name_fails(self) -> None:
        tools, _ = _make_tools()
        with pytest.raises(ValueError, match="unknown tool"):
            build_tools(
                tools,
                execute=NONE,
                descriptions={ToolName("typo"): "x"},
                results=StubResults(),
            )
