"""Tests for schema utilities."""

from typing import Literal, Union

import pytest
from pydantic import BaseModel, ValidationError

from neosian._foundation.shared.schema import (
    get_json_schema,
    get_schema_name,
    is_union_type,
    validate_json,
)


class SimpleModel(BaseModel):
    """Simple model for testing."""

    name: str
    value: int


class NestedChild(BaseModel):
    """Child model — forces Pydantic to emit $defs in the parent."""

    question: str
    options: list[str]


class NestedParent(BaseModel):
    """Parent with a nested model (list of children)."""

    questions: list[NestedChild]


class DeeplyNested(BaseModel):
    """Three levels: DeeplyNested -> NestedParent -> NestedChild."""

    quiz: NestedParent
    label: str


class TypeA(BaseModel):
    """Type A for Union testing."""

    type: Literal["a"]
    data_a: str


class TypeB(BaseModel):
    """Type B for Union testing."""

    type: Literal["b"]
    data_b: int


UnionAB = Union[TypeA, TypeB]  # noqa: UP007 - Testing typing.Union specifically


@pytest.mark.unit
class TestIsUnionType:
    """Tests for is_union_type function."""

    def test_basemodel_is_not_union(self) -> None:
        """BaseModel subclass should not be detected as union."""
        assert is_union_type(SimpleModel) is False

    def test_typing_union_is_union(self) -> None:
        """typing.Union should be detected as union."""
        assert is_union_type(UnionAB) is True

    def test_pipe_union_is_union(self) -> None:
        """Python 3.10+ pipe syntax union should be detected."""
        PipeUnion = TypeA | TypeB
        assert is_union_type(PipeUnion) is True


@pytest.mark.unit
class TestGetSchemaName:
    """Tests for get_schema_name function."""

    def test_basemodel_name(self) -> None:
        """BaseModel should return class name."""
        assert get_schema_name(SimpleModel) == "SimpleModel"

    def test_union_name(self) -> None:
        """Union should return member names joined with _or_."""
        name = get_schema_name(UnionAB)
        assert name == "TypeA_or_TypeB"

    def test_union_name_with_pipe(self) -> None:
        """Pipe union should return member names joined with _or_."""
        PipeUnion = TypeA | TypeB
        name = get_schema_name(PipeUnion)
        assert name == "TypeA_or_TypeB"


@pytest.mark.unit
class TestGetJsonSchema:
    """Tests for get_json_schema function."""

    def test_basemodel_schema(self) -> None:
        """BaseModel should produce valid JSON schema."""
        schema = get_json_schema(SimpleModel)
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert "value" in schema["properties"]

    def test_union_schema_is_wrapped(self) -> None:
        """Union should be wrapped in object with 'result' property."""
        schema = get_json_schema(UnionAB)
        # Unions are wrapped in an object with a "result" property
        assert schema["type"] == "object"
        assert "result" in schema["properties"]
        # The union structure is inside the result property
        result_schema = schema["properties"]["result"]
        has_union_structure = (
            "anyOf" in result_schema
            or "oneOf" in result_schema
            or "$ref" in result_schema
        )
        assert has_union_structure

    def test_union_schema_has_defs(self) -> None:
        """Union schema should have $defs at root level."""
        schema = get_json_schema(UnionAB)
        # $defs should be at root level (moved from nested)
        assert "$defs" in schema
        assert "TypeA" in schema["$defs"]
        assert "TypeB" in schema["$defs"]

    def test_union_schema_has_additional_properties_false(self) -> None:
        """Union schema should have additionalProperties: false on all objects."""
        schema = get_json_schema(UnionAB)
        # Root object
        assert schema["additionalProperties"] is False
        # Objects in $defs
        assert schema["$defs"]["TypeA"]["additionalProperties"] is False
        assert schema["$defs"]["TypeB"]["additionalProperties"] is False

    def test_pipe_union_schema(self) -> None:
        """Pipe union should produce wrapped JSON schema."""
        PipeUnion = TypeA | TypeB
        schema = get_json_schema(PipeUnion)
        assert schema["type"] == "object"
        assert "result" in schema["properties"]


def _objects_missing_additional_properties(
    node: object, path: str = "root"
) -> list[str]:
    """Paths of every object subschema lacking additionalProperties.

    LLM APIs reject object schemas without it — including nested models
    under $defs — so the correct result is always an empty list.
    """
    missing: list[str] = []
    if not isinstance(node, dict):
        return missing
    if node.get("type") == "object" and "additionalProperties" not in node:
        missing.append(path)
    for key, value in node.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            for name, sub in value.items():
                missing += _objects_missing_additional_properties(
                    sub, f"{path}.{key}.{name}"
                )
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            for index, sub in enumerate(value):
                missing += _objects_missing_additional_properties(
                    sub, f"{path}.{key}[{index}]"
                )
        elif key == "items":
            missing += _objects_missing_additional_properties(value, f"{path}.items")
    return missing


@pytest.mark.unit
class TestAdditionalPropertiesInvariant:
    """get_json_schema must set additionalProperties: false on EVERY object.

    Anthropic (and OpenAI/Groq strict mode) reject any object schema without
    it, including nested models Pydantic emits under $defs. Anthropic rejects
    them regardless of strict mode, so adapters cannot be relied on to patch
    the root — the invariant lives here.
    """

    def test_flat_basemodel_root_patched(self) -> None:
        """Regression guard: a flat model's root object carries the flag."""
        schema = get_json_schema(SimpleModel)
        assert schema["additionalProperties"] is False
        assert _objects_missing_additional_properties(schema) == []

    def test_nested_basemodel_defs_patched(self) -> None:
        """The reported bug: nested models under $defs were left unpatched."""
        schema = get_json_schema(NestedParent)

        assert "$defs" in schema  # Pydantic emits the child here
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["NestedChild"]["additionalProperties"] is False
        assert _objects_missing_additional_properties(schema) == []

    def test_deeply_nested_all_levels_patched(self) -> None:
        """Every level of a model -> model -> model chain is patched."""
        schema = get_json_schema(DeeplyNested)

        assert schema["additionalProperties"] is False
        for def_name in ("NestedParent", "NestedChild"):
            assert schema["$defs"][def_name]["additionalProperties"] is False
        assert _objects_missing_additional_properties(schema) == []

    def test_union_unchanged(self) -> None:
        """Unions already worked — behavior must not regress."""
        schema = get_json_schema(UnionAB)
        assert _objects_missing_additional_properties(schema) == []


@pytest.mark.unit
class TestValidateJson:
    """Tests for validate_json function."""

    def test_basemodel_validation(self) -> None:
        """BaseModel should validate JSON correctly."""
        result = validate_json(SimpleModel, '{"name": "test", "value": 42}')
        assert isinstance(result, SimpleModel)
        assert result.name == "test"
        assert result.value == 42

    def test_union_validation_type_a(self) -> None:
        """Union should validate wrapped JSON and return TypeA instance."""
        # LLM returns wrapped format: {"result": {...}}
        result = validate_json(UnionAB, '{"result": {"type": "a", "data_a": "hello"}}')
        assert isinstance(result, TypeA)
        assert result.type == "a"
        assert result.data_a == "hello"

    def test_union_validation_type_b(self) -> None:
        """Union should validate wrapped JSON and return TypeB instance."""
        # LLM returns wrapped format: {"result": {...}}
        result = validate_json(UnionAB, '{"result": {"type": "b", "data_b": 123}}')
        assert isinstance(result, TypeB)
        assert result.type == "b"
        assert result.data_b == 123

    def test_pipe_union_validation(self) -> None:
        """Pipe union should validate wrapped JSON correctly."""
        PipeUnion = TypeA | TypeB
        # LLM returns wrapped format: {"result": {...}}
        result = validate_json(PipeUnion, '{"result": {"type": "a", "data_a": "test"}}')
        assert isinstance(result, TypeA)
        assert result.data_a == "test"

    def test_invalid_json_raises(self) -> None:
        """Invalid JSON should raise validation error."""
        with pytest.raises(ValidationError):
            validate_json(SimpleModel, '{"name": "test"}')  # missing "value"

    def test_invalid_union_discriminator_raises(self) -> None:
        """Invalid discriminator value should raise validation error."""
        with pytest.raises(ValidationError):
            validate_json(UnionAB, '{"result": {"type": "c", "data": "invalid"}}')
