"""Tests for tool system base primitives."""

import pytest

from neosian._foundation.tools.base import (
    Tool,
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)


@pytest.mark.unit
class TestToolResult:
    """Test ToolResult dataclass."""

    def test_ok_result(self) -> None:
        """ToolResult.ok should create successful result."""
        result = ToolResult.ok("hello")
        assert result.success is True
        assert result.data == "hello"
        assert result.error is None

    def test_fail_result(self) -> None:
        """ToolResult.fail should create failed result."""
        result: ToolResult[str] = ToolResult.fail("something went wrong")
        assert result.success is False
        assert result.data is None
        assert result.error == "something went wrong"

    def test_ok_with_complex_data(self) -> None:
        """ToolResult.ok should work with complex types."""
        data = {"items": [1, 2, 3], "total": 3}
        result = ToolResult.ok(data)
        assert result.success is True
        assert result.data == data


@pytest.mark.unit
class TestToolDecorator:
    """Test @Tool decorator."""

    def test_basic_tool_creation(self) -> None:
        """@Tool should attach metadata to function."""

        @Tool(name="greet", description="Greet a person")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello, {name}!")

        metadata = get_tool_metadata(greet)
        assert metadata is not None
        assert metadata.name == "greet"
        assert metadata.description == "Greet a person"

    def test_tool_definition_extraction(self) -> None:
        """@Tool should create correct ToolDefinition."""

        @Tool(name="search", description="Search for items")
        async def search(
            query: str, limit: int = 10  # noqa: ARG001
        ) -> ToolResult[list[str]]:
            return ToolResult.ok([])

        definition = get_tool_definition(search)
        assert definition is not None
        assert definition.name == "search"
        assert definition.description == "Search for items"
        assert definition.parameters["type"] == "object"
        assert "query" in definition.parameters["properties"]
        assert "limit" in definition.parameters["properties"]

    def test_required_parameters(self) -> None:
        """Required params should be in required list."""

        @Tool(name="test", description="Test tool")
        async def test_func(
            required_arg: str, optional_arg: int = 5  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert "required_arg" in definition.parameters["required"]
        assert "optional_arg" not in definition.parameters["required"]

    def test_type_conversion_str(self) -> None:
        """String type should convert to JSON string."""

        @Tool(name="test", description="Test")
        async def test_func(arg: str) -> ToolResult[str]:
            return ToolResult.ok(arg)

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "string"

    def test_type_conversion_int(self) -> None:
        """Int type should convert to JSON integer."""

        @Tool(name="test", description="Test")
        async def test_func(arg: int) -> ToolResult[str]:
            return ToolResult.ok(str(arg))

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "integer"

    def test_type_conversion_float(self) -> None:
        """Float type should convert to JSON number."""

        @Tool(name="test", description="Test")
        async def test_func(arg: float) -> ToolResult[str]:
            return ToolResult.ok(str(arg))

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "number"

    def test_type_conversion_bool(self) -> None:
        """Bool type should convert to JSON boolean."""

        @Tool(name="test", description="Test")
        async def test_func(arg: bool) -> ToolResult[str]:
            return ToolResult.ok(str(arg))

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "boolean"

    def test_type_conversion_list(self) -> None:
        """List type should convert to JSON array."""

        @Tool(name="test", description="Test")
        async def test_func(arg: list[str]) -> ToolResult[str]:
            return ToolResult.ok(",".join(arg))

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "array"
        assert definition.parameters["properties"]["arg"]["items"]["type"] == "string"

    def test_type_conversion_dict(self) -> None:
        """Dict type should convert to JSON object."""

        @Tool(name="test", description="Test")
        async def test_func(arg: dict[str, int]) -> ToolResult[str]:
            return ToolResult.ok(str(arg))

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "object"
        assert (
            definition.parameters["properties"]["arg"]["additionalProperties"]["type"]
            == "integer"
        )


@pytest.mark.unit
class TestGetToolHelpers:
    """Test helper functions."""

    def test_get_metadata_on_non_tool(self) -> None:
        """get_tool_metadata should return None for non-tools."""

        async def regular_func() -> str:
            return "hello"

        assert get_tool_metadata(regular_func) is None

    def test_get_definition_on_non_tool(self) -> None:
        """get_tool_definition should return None for non-tools."""

        async def regular_func() -> str:
            return "hello"

        assert get_tool_definition(regular_func) is None
