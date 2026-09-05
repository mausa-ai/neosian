"""Tests for tool system base primitives.

The schema shapes are pydantic's since NF slice B (DESIGN §27.9): a
nullable optional is an `anyOf` with `null`, a TypedDict or Enum lives
under `$defs`, no property carries a `title`.
"""

# ruff: noqa: ARG001

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, Optional, TypedDict, Union

import pytest

from neosian._foundation.llm.base import ToolDefinition
from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.tools.base import (
    Tool,
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
    set_native_type,
)
from neosian._foundation.tools.result import FAILED_ENVELOPE_PREFIX


def _resolved(definition: ToolDefinition | None, name: str = "arg") -> dict[str, Any]:
    """One property's schema with a `$ref` into `$defs` inlined."""
    assert definition is not None
    prop = dict(definition.parameters["properties"][name])
    if "$ref" in prop:
        target = definition.parameters["$defs"][prop.pop("$ref").rsplit("/", 1)[1]]
        prop = {**target, **prop}
    return prop


def _prop(hint: Any) -> dict[str, Any]:
    """The schema one parameter typed `hint` gets."""

    async def probe(arg: Any) -> ToolResult[str]:
        return ToolResult.ok("ok")

    probe.__annotations__["arg"] = hint
    return _resolved(get_tool_definition(Tool(name="probe", description="p")(probe)))


def _nullable(prop: dict[str, Any]) -> dict[str, Any]:
    """The non-null branch of a nullable property, asserting the null one."""
    assert {"type": "null"} in prop["anyOf"]
    branches: list[dict[str, Any]] = [b for b in prop["anyOf"] if b != {"type": "null"}]
    assert len(branches) == 1
    return branches[0]


@pytest.mark.unit
class TestToolResult:
    """Test ToolResult dataclass."""

    def test_ok_result(self) -> None:
        """ToolResult.ok should create success result."""
        result = ToolResult.ok("data")
        assert result.success is True
        assert result.data == "data"
        assert result.error is None

    def test_fail_result(self) -> None:
        """ToolResult.fail should create error result."""
        result: ToolResult[str] = ToolResult.fail("error message")
        assert result.success is False
        assert result.data is None
        assert result.error == "error message"

    def test_ok_with_complex_data(self) -> None:
        """ToolResult.ok should handle complex data."""
        data = {"key": "value", "list": [1, 2, 3]}
        result = ToolResult.ok(data)
        assert result.success is True
        assert result.data == data

    def test_to_json_ignores_the_receipt(self) -> None:
        """The wire envelope is byte-identical with or without a receipt."""
        from neosian._foundation.memory.receipt import MemoryWriteReceipt

        receipt = MemoryWriteReceipt(
            command="create", mount_path="user", path="/user/a", version=1
        )
        with_receipt = ToolResult.ok("done", receipt=receipt)
        without: ToolResult[str] = ToolResult.ok("done")
        assert with_receipt.to_json() == without.to_json()
        assert "receipt" not in with_receipt.to_json()

    def test_every_failure_starts_with_the_pinned_prefix(self) -> None:
        """Anthropic's `is_error` reads the verdict off this prefix (LL-10)."""
        assert ToolResult.fail("x").to_json().startswith(FAILED_ENVELOPE_PREFIX)
        assert (
            ToolResult.fail("x", "hint", code="tool_execution_failed")
            .to_json()
            .startswith(FAILED_ENVELOPE_PREFIX)
        )
        assert not ToolResult.ok("x").to_json().startswith(FAILED_ENVELOPE_PREFIX)


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
        assert metadata.arguments is not None
        assert metadata.arguments.__name__ == "greet_arguments"

    def test_tool_definition_extraction(self) -> None:
        """@Tool should create correct ToolDefinition."""

        @Tool(name="search", description="Search for items")
        async def search(query: str, limit: int = 10) -> ToolResult[list[str]]:
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
            required_arg: str, optional_arg: int = 5
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert "required_arg" in definition.parameters["required"]
        assert "optional_arg" not in definition.parameters["required"]

    def test_tool_strict_defaults_false(self) -> None:
        @Tool(name="test", description="Test")
        async def test_func(arg: str) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.strict is False

    def test_tool_strict_true_propagates(self) -> None:
        @Tool(name="test", description="Test", strict=True)
        async def test_func(arg: str) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.strict is True

    def test_no_title_anywhere(self) -> None:
        """Titles are tokens, not contract — none on the root, a property
        or a `$defs` entry."""

        class Item(TypedDict):
            title: str  # a property literally named title survives

        @Tool(name="test", description="Test")
        async def test_func(items: list[Item], count: int) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        text = json.dumps(definition.parameters)
        assert text.count('"title":') == 1
        assert "title" in definition.parameters["$defs"]["Item"]["properties"]


@pytest.mark.unit
class TestTypeConversion:
    """Python types to the JSON Schema the model gets."""

    def test_scalars(self) -> None:
        assert _prop(str) == {"type": "string"}
        assert _prop(int) == {"type": "integer"}
        assert _prop(float) == {"type": "number"}
        assert _prop(bool) == {"type": "boolean"}

    def test_list_and_dict(self) -> None:
        assert _prop(list[str]) == {"type": "array", "items": {"type": "string"}}
        assert _prop(dict[str, int]) == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_optional_keeps_the_null_branch(self) -> None:
        """`int | None` is nullable on the wire (TG-17), old and new spellings."""
        for hint in (int | None, Optional[int]):  # noqa: UP045
            prop = _prop(hint)
            assert _nullable(prop) == {"type": "integer"}
        assert _nullable(_prop(str | None)) == {"type": "string"}
        assert _nullable(_prop(list[str] | None)) == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_optional_without_a_default_stays_required(self) -> None:
        """Nullable is not optional: no default means the model must pass it."""

        @Tool(name="test", description="Test")
        async def test_func(arg: int | None) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["required"] == ["arg"]
        assert "default" not in definition.parameters["properties"]["arg"]

    def test_union_of_types_is_any_of(self) -> None:
        for hint in (int | str, Union[int, str]):  # noqa: UP007
            prop = _prop(hint)
            assert {"type": "integer"} in prop["anyOf"]
            assert {"type": "string"} in prop["anyOf"]


@pytest.mark.unit
class TestGetToolHelpers:
    """Test helper functions."""

    def test_get_metadata_on_non_tool(self) -> None:
        async def regular_func() -> str:
            return "hello"

        assert get_tool_metadata(regular_func) is None

    def test_get_definition_on_non_tool(self) -> None:
        async def regular_func() -> str:
            return "hello"

        assert get_tool_definition(regular_func) is None

    def test_set_native_type_requires_decoration(self) -> None:
        async def regular_func() -> ToolResult[str]:
            return ToolResult.ok("hello")

        with pytest.raises(ValueError, match="not decorated"):
            set_native_type(regular_func, "memory_20250818")

    def test_set_native_type_marks_the_definition(self) -> None:
        @Tool(name="probe", description="A probe")
        async def probe() -> ToolResult[str]:
            return ToolResult.ok("ok")

        returned = set_native_type(probe, "memory_20250818")
        assert returned is probe
        definition = get_tool_definition(probe)
        assert definition is not None
        assert definition.native_type == "memory_20250818"


@pytest.mark.unit
class TestLiteralTypeConversion:
    """Literal types to enums."""

    def test_literal_strings(self) -> None:
        assert _prop(Literal["a", "b", "c"]) == {
            "type": "string",
            "enum": ["a", "b", "c"],
        }

    def test_literal_integers(self) -> None:
        assert _prop(Literal[1, 2, 3]) == {"type": "integer", "enum": [1, 2, 3]}

    def test_literal_single_value(self) -> None:
        assert _prop(Literal["only"]) == {"type": "string", "const": "only"}

    def test_literal_in_tool(self) -> None:
        @Tool(name="test", description="Test")
        async def test_func(mode: Literal["fast", "slow"] = "fast") -> ToolResult[str]:
            return ToolResult.ok("ok")

        prop = _resolved(get_tool_definition(test_func), "mode")
        assert prop["type"] == "string"
        assert prop["enum"] == ["fast", "slow"]
        assert prop["default"] == "fast"


@pytest.mark.unit
class TestEnumClassConversion:
    """Enum classes to enums, under `$defs`."""

    def test_string_enum(self) -> None:
        class Color(str, Enum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        prop = _prop(Color)
        assert prop["type"] == "string"
        assert prop["enum"] == ["red", "green", "blue"]

    def test_int_enum(self) -> None:
        class Priority(int, Enum):
            LOW = 1
            MEDIUM = 2
            HIGH = 3

        prop = _prop(Priority)
        assert prop["type"] == "integer"
        assert prop["enum"] == [1, 2, 3]

    def test_enum_in_tool(self) -> None:
        class Status(str, Enum):
            PENDING = "pending"
            DONE = "done"

        @Tool(name="test", description="Test")
        async def test_func(status: Status) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["status"] == {
            "$ref": "#/$defs/Status"
        }
        prop = _resolved(definition, "status")
        assert prop["type"] == "string"
        assert prop["enum"] == ["pending", "done"]


@pytest.mark.unit
class TestAnnotatedConstraints:
    """neosian's constraint vocabulary, translated for pydantic (TG-10)."""

    def test_desc_constraint(self) -> None:
        assert _prop(Annotated[str, Desc("A query string")]) == {
            "type": "string",
            "description": "A query string",
        }

    def test_min_max(self) -> None:
        assert _prop(Annotated[int, Min(1)]) == {"type": "integer", "minimum": 1}
        assert _prop(Annotated[int, Max(100)]) == {"type": "integer", "maximum": 100}
        assert _prop(Annotated[int, Min(1), Max(100)]) == {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
        }

    def test_minlen_maxlen_by_type(self) -> None:
        assert _prop(Annotated[str, MinLen(1)]) == {"type": "string", "minLength": 1}
        assert _prop(Annotated[str, MaxLen(100)]) == {
            "type": "string",
            "maxLength": 100,
        }
        assert _prop(Annotated[list[str], MinLen(1)]) == {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        }
        assert _prop(Annotated[list[str], MaxLen(10)]) == {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
        }

    def test_pattern_constraint(self) -> None:
        assert _prop(Annotated[str, Pattern(r"^[a-z]+$")]) == {
            "type": "string",
            "pattern": r"^[a-z]+$",
        }

    def test_multiple_constraints(self) -> None:
        prop = _prop(
            Annotated[
                str, Desc("Username"), MinLen(3), MaxLen(20), Pattern(r"^[a-z]+$")
            ]
        )
        assert prop == {
            "type": "string",
            "description": "Username",
            "minLength": 3,
            "maxLength": 20,
            "pattern": r"^[a-z]+$",
        }

    def test_annotated_with_literal(self) -> None:
        prop = _prop(Annotated[Literal["a", "b"], Desc("Choose a or b")])
        assert prop["type"] == "string"
        assert prop["enum"] == ["a", "b"]
        assert prop["description"] == "Choose a or b"

    def test_constraints_in_tool(self) -> None:
        @Tool(name="search", description="Search items")
        async def search(
            query: Annotated[str, Desc("Search query")],
            limit: Annotated[int, Desc("Max results"), Min(1), Max(100)] = 10,
        ) -> ToolResult[list[str]]:
            return ToolResult.ok([])

        definition = get_tool_definition(search)
        assert _resolved(definition, "query") == {
            "type": "string",
            "description": "Search query",
        }
        assert _resolved(definition, "limit") == {
            "type": "integer",
            "description": "Max results",
            "minimum": 1,
            "maximum": 100,
            "default": 10,
        }


@pytest.mark.unit
class TestDefaultValues:
    """Default values reach the schema."""

    def test_defaults_are_included(self) -> None:
        @Tool(name="test", description="Test")
        async def test_func(
            s: str = "default",
            i: int = 42,
            b: bool = False,
            n: str | None = None,
            items: list[str] = [],  # noqa: B006
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        properties = definition.parameters["properties"]
        assert properties["s"]["default"] == "default"
        assert properties["i"]["default"] == 42
        assert properties["b"]["default"] is False
        assert properties["n"]["default"] is None
        assert properties["items"]["default"] == []
        assert definition.parameters.get("required", []) == []

    def test_no_default_no_key(self) -> None:
        @Tool(name="test", description="Test")
        async def test_func(arg: str) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert "default" not in definition.parameters["properties"]["arg"]


@pytest.mark.unit
class TestAdditionalPropertiesFalse:
    """Every object the model sees is closed."""

    def test_additional_properties_false(self) -> None:
        @Tool(name="test", description="Test")
        async def test_func(a: str, b: int, c: bool = True) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["additionalProperties"] is False


@pytest.mark.unit
class TestComplexToolSchema:
    """A complete schema matching the target format."""

    def test_full_schema_generation(self) -> None:
        @Tool(
            name="generate_image",
            description=(
                "Generate an image from text. "
                "Use when user asks for image creation. "
                "Do NOT use for editing existing images."
            ),
        )
        async def generate_image(
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
        assert definition.name == "generate_image"
        assert "Generate an image from text" in definition.description
        assert definition.parameters["type"] == "object"
        assert definition.parameters["additionalProperties"] is False
        assert definition.parameters["required"] == ["prompt"]
        assert _resolved(definition, "prompt") == {
            "type": "string",
            "description": "Detailed description of the image",
        }
        assert _resolved(definition, "quality") == {
            "type": "string",
            "enum": ["standard", "high"],
            "default": "standard",
            "description": "Image quality. 'high' costs 2x more.",
        }
        assert _resolved(definition, "aspect_ratio") == {
            "type": "string",
            "enum": ["1:1", "16:9", "9:16"],
            "default": "1:1",
        }
        assert _resolved(definition, "num_images") == {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "default": 1,
            "description": "Number of images",
        }
        assert _resolved(definition, "tags") == {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "default": [],
            "description": "Optional tags",
        }


@pytest.mark.unit
class TestTypedDictConversion:
    """TypedDicts are closed objects under `$defs`."""

    def test_basic_typeddict(self) -> None:
        class Person(TypedDict):
            name: str
            age: int

        prop = _prop(Person)
        assert prop["type"] == "object"
        assert prop["properties"]["name"]["type"] == "string"
        assert prop["properties"]["age"]["type"] == "integer"
        assert set(prop["required"]) == {"name", "age"}
        assert prop["additionalProperties"] is False

    def test_typeddict_with_optional_fields(self) -> None:
        class Config(TypedDict):
            name: str
            description: NotRequired[str]

        prop = _prop(Config)
        assert prop["properties"]["description"]["type"] == "string"
        assert prop["required"] == ["name"]

    def test_typeddict_in_list(self) -> None:
        class Item(TypedDict):
            id: int
            name: str

        @Tool(name="test", description="Test")
        async def test_func(items: list[Item]) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        items = definition.parameters["properties"]["items"]
        assert items == {"type": "array", "items": {"$ref": "#/$defs/Item"}}
        item = definition.parameters["$defs"]["Item"]
        assert item["properties"]["id"]["type"] == "integer"
        assert set(item["required"]) == {"id", "name"}
        assert item["additionalProperties"] is False


@pytest.mark.unit
class TestDecorationRejections:
    """Signatures the schema cannot state truthfully are rejected at decoration."""

    def test_var_positional_rejected(self) -> None:
        """``*args`` cannot be supplied by keyword dispatch (TG-8)."""
        with pytest.raises(ConfigurationError, match=r"\*args"):

            @Tool(name="test", description="Test")
            async def test_func(*args: str) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_var_keyword_rejected(self) -> None:
        """``**kwargs`` is not a parameter the model can name (TG-8)."""
        with pytest.raises(ConfigurationError, match=r"\*\*kwargs"):

            @Tool(name="test", description="Test")
            async def test_func(**kwargs: str) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_positional_only_rejected(self) -> None:
        """A positional-only parameter can never be bound from arguments (TG-8)."""
        with pytest.raises(ConfigurationError, match="query"):

            @Tool(name="test", description="Test")
            async def test_func(query: str, /) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_unannotated_rejected(self) -> None:
        """No annotation, no schema — never a silent `string` (TG-19)."""
        with pytest.raises(ConfigurationError, match="no type annotation"):

            @Tool(name="test", description="Test")
            async def test_func(query) -> ToolResult[str]:  # type: ignore[no-untyped-def]
                return ToolResult.ok("ok")

    def test_undeclared_params_prose_rejected(self) -> None:
        """A `params=` name the signature lacks is a typo, not silence."""
        with pytest.raises(ConfigurationError, match="does not declare.*'querry'"):

            @Tool(name="test", description="Test", params={"querry": "The query."})
            async def test_func(query: str) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_path_default_rejected(self) -> None:
        """A default that is not JSON never reaches the schema (TG-18)."""
        with pytest.raises(ConfigurationError, match="root"):

            @Tool(name="test", description="Test")
            async def test_func(root: Path = Path("/tmp")) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_enum_member_default_rejected(self) -> None:
        """A plain Enum member is not JSON-serialisable (TG-18)."""

        class Mode(Enum):
            FAST = 1
            SLOW = 2

        with pytest.raises(ConfigurationError, match="mode"):

            @Tool(name="test", description="Test")
            async def test_func(mode: Mode = Mode.FAST) -> ToolResult[str]:
                return ToolResult.ok("ok")

    def test_str_enum_default_allowed(self) -> None:
        """A str-Enum member serialises as its value and stays allowed."""

        class Mode(str, Enum):
            FAST = "fast"
            SLOW = "slow"

        @Tool(name="test", description="Test")
        async def test_func(mode: Mode = Mode.FAST) -> ToolResult[str]:
            return ToolResult.ok("ok")

        definition = get_tool_definition(test_func)
        assert definition is not None
        assert definition.parameters["properties"]["mode"]["default"] == "fast"


@pytest.mark.unit
class TestToolResultCode:
    """`code` names the failure class in-band (NF #171, TG-40)."""

    def test_coded_failure_is_on_the_wire(self) -> None:
        result: ToolResult[str] = ToolResult.fail("bad", code="tool_invalid_arguments")
        assert result.code == "tool_invalid_arguments"
        assert json.loads(result.to_json()) == {
            "success": False,
            "error": "bad",
            "code": "tool_invalid_arguments",
        }

    def test_uncoded_failure_and_success_carry_no_key(self) -> None:
        assert "code" not in json.loads(ToolResult.fail("bad").to_json())
        assert "code" not in json.loads(ToolResult.ok("fine").to_json())
