"""Tests for tool system base primitives."""

# ruff: noqa: ARG001

from enum import Enum
from typing import Annotated, Literal, NotRequired, Optional, TypedDict, Union

import pytest

from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.tools.base import (
    Tool,
    ToolResult,
    _python_type_to_json_schema,
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

    def test_type_conversion_optional_int(self) -> None:
        """Optional[int] should convert to JSON integer."""

        @Tool(name="test", description="Test")
        async def test_func(
            arg: int | None = None,  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "integer"

    def test_type_conversion_optional_str(self) -> None:
        """Optional[str] should convert to JSON string."""

        @Tool(name="test", description="Test")
        async def test_func(
            arg: str | None = None,  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "string"

    def test_type_conversion_union_pipe_syntax(self) -> None:
        """int | None should convert to JSON integer (Python 3.10+ syntax)."""

        @Tool(name="test", description="Test")
        async def test_func(arg: int | None = None) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["type"] == "integer"

    def test_type_conversion_union_multiple_types(self) -> None:
        """Union[int, str] should convert to anyOf schema."""

        @Tool(name="test", description="Test")
        async def test_func(arg: int | str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        prop = definition.parameters["properties"]["arg"]
        assert "anyOf" in prop
        assert {"type": "integer"} in prop["anyOf"]
        assert {"type": "string"} in prop["anyOf"]

    def test_type_conversion_optional_list(self) -> None:
        """Optional[list[str]] should convert to JSON array."""

        @Tool(name="test", description="Test")
        async def test_func(
            arg: list[str] | None = None,  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        prop = definition.parameters["properties"]["arg"]
        assert prop["type"] == "array"
        assert prop["items"]["type"] == "string"


@pytest.mark.unit
class TestPythonTypeToJsonSchema:
    """Test _python_type_to_json_schema function directly.

    Note: Some tests use Optional[] and Union[] syntax intentionally
    to verify backward compatibility with older typing styles.
    """

    def test_optional_int(self) -> None:
        """Optional[int] should return integer schema."""
        # Intentionally testing Optional[] backward compatibility
        assert _python_type_to_json_schema(Optional[int]) == {  # noqa: UP045
            "type": "integer"
        }

    def test_optional_float(self) -> None:
        """Optional[float] should return number schema."""
        # Intentionally testing Optional[] backward compatibility
        assert _python_type_to_json_schema(Optional[float]) == {  # noqa: UP045
            "type": "number"
        }

    def test_optional_bool(self) -> None:
        """Optional[bool] should return boolean schema."""
        # Intentionally testing Optional[] backward compatibility
        assert _python_type_to_json_schema(Optional[bool]) == {  # noqa: UP045
            "type": "boolean"
        }

    def test_pipe_syntax_int_none(self) -> None:
        """int | None should return integer schema."""
        assert _python_type_to_json_schema(int | None) == {"type": "integer"}

    def test_pipe_syntax_str_none(self) -> None:
        """str | None should return string schema."""
        assert _python_type_to_json_schema(str | None) == {"type": "string"}

    def test_union_int_str(self) -> None:
        """Union[int, str] should return anyOf schema."""
        # noqa: UP007 - intentionally testing Union[] backward compatibility
        result = _python_type_to_json_schema(Union[int, str])  # noqa: UP007
        assert "anyOf" in result
        assert {"type": "integer"} in result["anyOf"]
        assert {"type": "string"} in result["anyOf"]

    def test_pipe_syntax_int_str(self) -> None:
        """int | str should return anyOf schema."""
        result = _python_type_to_json_schema(int | str)
        assert "anyOf" in result
        assert {"type": "integer"} in result["anyOf"]
        assert {"type": "string"} in result["anyOf"]

    def test_nested_optional_list(self) -> None:
        """Optional[list[int]] should return array schema."""
        # noqa: UP045 - intentionally testing Optional[] backward compatibility
        result = _python_type_to_json_schema(Optional[list[int]])  # noqa: UP045
        assert result == {"type": "array", "items": {"type": "integer"}}


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


@pytest.mark.unit
class TestLiteralTypeConversion:
    """Test Literal type to enum conversion."""

    def test_literal_strings(self) -> None:
        """Literal[str, ...] should convert to enum."""
        result = _python_type_to_json_schema(Literal["a", "b", "c"])
        assert result["type"] == "string"
        assert result["enum"] == ["a", "b", "c"]

    def test_literal_integers(self) -> None:
        """Literal[int, ...] should convert to integer enum."""
        result = _python_type_to_json_schema(Literal[1, 2, 3])
        assert result["type"] == "integer"
        assert result["enum"] == [1, 2, 3]

    def test_literal_single_value(self) -> None:
        """Literal with single value should work."""
        result = _python_type_to_json_schema(Literal["only"])
        assert result["type"] == "string"
        assert result["enum"] == ["only"]

    def test_literal_in_tool(self) -> None:
        """Literal type should work in @Tool decorated function."""

        @Tool(name="test", description="Test")
        async def test_func(
            mode: Literal["fast", "slow"] = "fast",  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        prop = definition.parameters["properties"]["mode"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["fast", "slow"]
        assert prop["default"] == "fast"


@pytest.mark.unit
class TestEnumClassConversion:
    """Test Enum class to enum conversion."""

    def test_string_enum(self) -> None:
        """String Enum should convert to string enum."""

        class Color(str, Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        result = _python_type_to_json_schema(Color)
        assert result["type"] == "string"
        assert result["enum"] == ["red", "green", "blue"]

    def test_int_enum(self) -> None:
        """Int Enum should convert to integer enum."""

        class Priority(int, Enum):
            LOW = 1
            MEDIUM = 2
            HIGH = 3

        result = _python_type_to_json_schema(Priority)
        assert result["type"] == "integer"
        assert result["enum"] == [1, 2, 3]

    def test_enum_in_tool(self) -> None:
        """Enum class should work in @Tool decorated function."""

        class Status(str, Enum):
            PENDING = "pending"
            DONE = "done"

        @Tool(name="test", description="Test")
        async def test_func(status: Status) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        prop = definition.parameters["properties"]["status"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["pending", "done"]


@pytest.mark.unit
class TestAnnotatedConstraints:
    """Test Annotated type with constraint metadata."""

    def test_desc_constraint(self) -> None:
        """Desc should add description to schema."""
        result = _python_type_to_json_schema(Annotated[str, Desc("A query string")])
        assert result["type"] == "string"
        assert result["description"] == "A query string"

    def test_min_constraint(self) -> None:
        """Min should add minimum to schema."""
        result = _python_type_to_json_schema(Annotated[int, Min(1)])
        assert result["type"] == "integer"
        assert result["minimum"] == 1

    def test_max_constraint(self) -> None:
        """Max should add maximum to schema."""
        result = _python_type_to_json_schema(Annotated[int, Max(100)])
        assert result["type"] == "integer"
        assert result["maximum"] == 100

    def test_min_max_combined(self) -> None:
        """Min and Max should combine."""
        result = _python_type_to_json_schema(Annotated[int, Min(1), Max(100)])
        assert result["type"] == "integer"
        assert result["minimum"] == 1
        assert result["maximum"] == 100

    def test_minlen_string(self) -> None:
        """MinLen should add minLength for strings."""
        result = _python_type_to_json_schema(Annotated[str, MinLen(1)])
        assert result["type"] == "string"
        assert result["minLength"] == 1

    def test_maxlen_string(self) -> None:
        """MaxLen should add maxLength for strings."""
        result = _python_type_to_json_schema(Annotated[str, MaxLen(100)])
        assert result["type"] == "string"
        assert result["maxLength"] == 100

    def test_minlen_array(self) -> None:
        """MinLen should add minItems for arrays."""
        result = _python_type_to_json_schema(Annotated[list[str], MinLen(1)])
        assert result["type"] == "array"
        assert result["minItems"] == 1

    def test_maxlen_array(self) -> None:
        """MaxLen should add maxItems for arrays."""
        result = _python_type_to_json_schema(Annotated[list[str], MaxLen(10)])
        assert result["type"] == "array"
        assert result["maxItems"] == 10

    def test_pattern_constraint(self) -> None:
        """Pattern should add pattern to schema."""
        result = _python_type_to_json_schema(Annotated[str, Pattern(r"^[a-z]+$")])
        assert result["type"] == "string"
        assert result["pattern"] == r"^[a-z]+$"

    def test_multiple_constraints(self) -> None:
        """Multiple constraints should all be applied."""
        result = _python_type_to_json_schema(
            Annotated[
                str, Desc("Username"), MinLen(3), MaxLen(20), Pattern(r"^[a-z]+$")
            ]
        )
        assert result["type"] == "string"
        assert result["description"] == "Username"
        assert result["minLength"] == 3
        assert result["maxLength"] == 20
        assert result["pattern"] == r"^[a-z]+$"

    def test_annotated_with_literal(self) -> None:
        """Annotated[Literal[...], Desc(...)] should work."""
        result = _python_type_to_json_schema(
            Annotated[Literal["a", "b"], Desc("Choose a or b")]
        )
        assert result["type"] == "string"
        assert result["enum"] == ["a", "b"]
        assert result["description"] == "Choose a or b"

    def test_constraints_in_tool(self) -> None:
        """Annotated constraints should work in @Tool decorated function."""

        @Tool(name="search", description="Search items")
        async def search(
            query: Annotated[str, Desc("Search query")],  # noqa: ARG001
            limit: Annotated[
                int, Desc("Max results"), Min(1), Max(100)
            ] = 10,  # noqa: ARG001
        ) -> ToolResult[list[str]]:
            return ToolResult.ok([])

        definition = get_tool_definition(search)
        assert definition is not None

        query_prop = definition.parameters["properties"]["query"]
        assert query_prop["type"] == "string"
        assert query_prop["description"] == "Search query"

        limit_prop = definition.parameters["properties"]["limit"]
        assert limit_prop["type"] == "integer"
        assert limit_prop["description"] == "Max results"
        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == 100
        assert limit_prop["default"] == 10


@pytest.mark.unit
class TestDefaultValues:
    """Test default value inclusion in schema."""

    def test_default_string(self) -> None:
        """String default should be included."""

        @Tool(name="test", description="Test")
        async def test_func(arg: str = "default") -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["default"] == "default"

    def test_default_int(self) -> None:
        """Int default should be included."""

        @Tool(name="test", description="Test")
        async def test_func(arg: int = 42) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["default"] == 42

    def test_default_bool(self) -> None:
        """Bool default should be included."""

        @Tool(name="test", description="Test")
        async def test_func(arg: bool = False) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["default"] is False

    def test_default_none(self) -> None:
        """None default should be included."""

        @Tool(name="test", description="Test")
        async def test_func(
            arg: str | None = None,  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["default"] is None

    def test_default_list(self) -> None:
        """List default should be included."""

        @Tool(name="test", description="Test")
        async def test_func(
            arg: list[str] = [],  # noqa: B006, ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["arg"]["default"] == []

    def test_no_default_no_key(self) -> None:
        """Required params should not have default key."""

        @Tool(name="test", description="Test")
        async def test_func(arg: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert "default" not in definition.parameters["properties"]["arg"]


@pytest.mark.unit
class TestAdditionalPropertiesFalse:
    """Test additionalProperties: false in schema."""

    def test_additional_properties_false(self) -> None:
        """Schema should have additionalProperties: false."""

        @Tool(name="test", description="Test")
        async def test_func(arg: str) -> ToolResult[str]:  # noqa: ARG001
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["additionalProperties"] is False

    def test_additional_properties_with_multiple_params(self) -> None:
        """additionalProperties should be false with multiple params."""

        @Tool(name="test", description="Test")
        async def test_func(
            a: str, b: int, c: bool = True  # noqa: ARG001
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["additionalProperties"] is False


@pytest.mark.unit
class TestComplexToolSchema:
    """Test complex tool schemas matching the target format."""

    def test_full_schema_generation(self) -> None:
        """Test complete schema matching target format."""

        class Quality(str, Enum):
            STANDARD = "standard"
            HIGH = "high"

        @Tool(
            name="generate_image",
            description=(
                "Generate an image from text. "
                "Use when user asks for image creation. "
                "Do NOT use for editing existing images."
            ),
        )
        async def generate_image(  # noqa: ARG001
            prompt: Annotated[str, Desc("Detailed description of the image")],
            quality: Annotated[
                Literal["standard", "high"],
                Desc("Image quality. 'high' costs 2x more."),
            ] = "standard",
            aspect_ratio: Literal["1:1", "16:9", "9:16"] = "1:1",
            num_images: Annotated[int, Desc("Number of images"), Min(1), Max(4)] = 1,
            tags: Annotated[
                list[str], Desc("Optional tags"), MaxLen(10)
            ] = [],  # noqa: B006
        ) -> ToolResult[list[str]]:
            return ToolResult.ok([])

        definition = get_tool_definition(generate_image)
        assert definition is not None

        # Check top-level
        assert definition.name == "generate_image"
        assert "Generate an image from text" in definition.description
        assert definition.parameters["type"] == "object"
        assert definition.parameters["additionalProperties"] is False
        assert definition.parameters["required"] == ["prompt"]

        # Check prompt
        prompt = definition.parameters["properties"]["prompt"]
        assert prompt["type"] == "string"
        assert prompt["description"] == "Detailed description of the image"
        assert "default" not in prompt

        # Check quality
        quality = definition.parameters["properties"]["quality"]
        assert quality["type"] == "string"
        assert quality["enum"] == ["standard", "high"]
        assert quality["default"] == "standard"
        assert quality["description"] == "Image quality. 'high' costs 2x more."

        # Check aspect_ratio
        aspect = definition.parameters["properties"]["aspect_ratio"]
        assert aspect["type"] == "string"
        assert aspect["enum"] == ["1:1", "16:9", "9:16"]
        assert aspect["default"] == "1:1"

        # Check num_images
        num = definition.parameters["properties"]["num_images"]
        assert num["type"] == "integer"
        assert num["minimum"] == 1
        assert num["maximum"] == 4
        assert num["default"] == 1
        assert num["description"] == "Number of images"

        # Check tags
        tags = definition.parameters["properties"]["tags"]
        assert tags["type"] == "array"
        assert tags["items"]["type"] == "string"
        assert tags["maxItems"] == 10
        assert tags["default"] == []
        assert tags["description"] == "Optional tags"


@pytest.mark.unit
class TestTypedDictConversion:
    """Test TypedDict to JSON Schema conversion."""

    def test_basic_typeddict(self) -> None:
        """TypedDict should convert to object with properties."""

        class Person(TypedDict):
            name: str
            age: int

        result = _python_type_to_json_schema(Person)
        assert result["type"] == "object"
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["age"]["type"] == "integer"
        assert set(result["required"]) == {"name", "age"}
        assert result["additionalProperties"] is False

    def test_typeddict_with_optional_fields(self) -> None:
        """TypedDict with NotRequired fields should have correct required list."""

        class Config(TypedDict):
            name: str
            description: NotRequired[str]

        result = _python_type_to_json_schema(Config)
        assert result["type"] == "object"
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["description"]["type"] == "string"
        assert result["required"] == ["name"]

    def test_typeddict_with_literal(self) -> None:
        """TypedDict with Literal field should have enum."""

        class Task(TypedDict):
            content: str
            status: Literal["pending", "done"]

        result = _python_type_to_json_schema(Task)
        assert result["type"] == "object"
        assert result["properties"]["content"]["type"] == "string"
        assert result["properties"]["status"]["type"] == "string"
        assert result["properties"]["status"]["enum"] == ["pending", "done"]
        assert set(result["required"]) == {"content", "status"}

    def test_typeddict_in_list(self) -> None:
        """list[TypedDict] should convert to array of objects."""

        class Item(TypedDict):
            id: int
            name: str

        result = _python_type_to_json_schema(list[Item])
        assert result["type"] == "array"
        assert result["items"]["type"] == "object"
        assert result["items"]["properties"]["id"]["type"] == "integer"
        assert result["items"]["properties"]["name"]["type"] == "string"
        assert set(result["items"]["required"]) == {"id", "name"}

    def test_typeddict_in_tool(self) -> None:
        """TypedDict should work in @Tool decorated function."""

        class TodoItem(TypedDict):
            content: str
            status: Literal["pending", "in_progress", "completed"]

        @Tool(name="update_todos", description="Update todo list")
        async def update_todos(
            todos: list[TodoItem],  # noqa: ARG001
        ) -> ToolResult[list[dict[str, str]]]:
            return ToolResult.ok([])

        definition = get_tool_definition(update_todos)
        assert definition is not None

        # Check todos parameter
        todos_prop = definition.parameters["properties"]["todos"]
        assert todos_prop["type"] == "array"

        # Check item schema
        item_schema = todos_prop["items"]
        assert item_schema["type"] == "object"
        assert item_schema["properties"]["content"]["type"] == "string"
        assert item_schema["properties"]["status"]["type"] == "string"
        assert item_schema["properties"]["status"]["enum"] == [
            "pending",
            "in_progress",
            "completed",
        ]
        assert set(item_schema["required"]) == {"content", "status"}
        assert item_schema["additionalProperties"] is False
