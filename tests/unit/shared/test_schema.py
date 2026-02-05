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
