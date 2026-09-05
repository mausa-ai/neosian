"""The tool schema is what the model gets (NF slice B, DESIGN §27.9).

Nested models, `datetime`/`UUID`, pydantic's own constraint vocabulary,
`Args:` lifting and `params=`, validation with the repairable failure —
all keyless, all through the decorator and the agent path.
"""

# ruff: noqa: ARG001

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import annotated_types
import pytest
from pydantic import BaseModel, Field, ValidationError

from neosian import Agent, AgentConfig, Desc, Min, Tool, ToolResult
from neosian._foundation.agent.tool_exec import execute_tool
from neosian._foundation.llm.base import ToolCall, ToolDefinition
from neosian._foundation.shared.schema import strict_schema
from neosian._foundation.shared.types import ToolCallId, ToolName
from neosian._foundation.tools.base import (
    attach_tool_metadata,
    get_tool_definition,
    get_tool_metadata,
)
from neosian._foundation.tools.schema import (
    lift_args_docstring,
    rejection,
    validate_arguments,
)


class Address(BaseModel):
    street: str
    zip: Annotated[str, Field(pattern=r"^\d{5}$")]


@dataclass
class Window:
    start: int
    end: int


def _parameters(func: Any) -> dict[str, Any]:
    definition = get_tool_definition(func)
    assert definition is not None
    return definition.parameters


def _model(func: Any) -> type[BaseModel]:
    metadata = get_tool_metadata(func)
    assert metadata is not None and metadata.arguments is not None
    return metadata.arguments


def _agent(tool: Any) -> Agent:
    router = MagicMock()
    router.available_providers.return_value = []
    with patch("neosian._foundation.agent.base.ProviderRouter", return_value=router):
        return Agent(config=AgentConfig(system_prompt="x", tools=[tool]))


@pytest.mark.unit
class TestTheSchemaTheModelGets:
    """TG-1: no type is silently a string."""

    def test_nested_model_is_a_ref_into_defs(self) -> None:
        @Tool(name="ship", description="Ship")
        async def ship(to: Address) -> ToolResult[str]:
            return ToolResult.ok("ok")

        parameters = _parameters(ship)
        assert parameters["properties"]["to"] == {"$ref": "#/$defs/Address"}
        address = parameters["$defs"]["Address"]
        assert address["properties"]["zip"] == {"type": "string", "pattern": r"^\d{5}$"}
        assert address["additionalProperties"] is False
        assert "title" not in address

    def test_dataclass_datetime_uuid_decimal(self) -> None:
        @Tool(name="probe", description="Probe")
        async def probe(
            window: Window, at: datetime, ref: UUID, amount: Decimal
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        properties = _parameters(probe)["properties"]
        assert properties["window"] == {"$ref": "#/$defs/Window"}
        assert properties["at"] == {"type": "string", "format": "date-time"}
        assert properties["ref"] == {"type": "string", "format": "uuid"}
        number, text = properties["amount"]["anyOf"]
        assert number == {"type": "number"}
        assert text["type"] == "string" and "pattern" in text

    def test_tuple_set_and_any(self) -> None:
        @Tool(name="probe", description="Probe")
        async def probe(
            pair: tuple[int, int], tags: set[str], blob: Any
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        properties = _parameters(probe)["properties"]
        assert properties["pair"]["prefixItems"] == [
            {"type": "integer"},
            {"type": "integer"},
        ]
        assert properties["tags"] == {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        }
        assert properties["blob"] == {}

    def test_pydantic_vocabulary_is_honoured(self) -> None:
        """`Field(ge=)` and `annotated_types` land beside `Min` (TG-10)."""

        @Tool(name="probe", description="Probe")
        async def probe(
            a: Annotated[int, Field(ge=1, description="A")],
            b: Annotated[int, annotated_types.Gt(0)],
            c: Annotated[int, Min(2), annotated_types.Lt(9)],
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        properties = _parameters(probe)["properties"]
        assert properties["a"] == {"type": "integer", "minimum": 1, "description": "A"}
        assert properties["b"] == {"type": "integer", "exclusiveMinimum": 0}
        assert properties["c"] == {
            "type": "integer",
            "minimum": 2,
            "exclusiveMaximum": 9,
        }


@pytest.mark.unit
class TestParameterProse:
    """TG-12: `Desc()` > `params=` > `Args:`."""

    def test_args_docstring_is_lifted(self) -> None:
        @Tool(name="read", description="Read")
        async def read(path: str, lines: int = 10) -> ToolResult[str]:
            """Read a file.

            Args:
                path: The file to read,
                    relative to the root.
                lines (int): How many lines.

            Returns:
                The text.
            """
            return ToolResult.ok("ok")

        properties = _parameters(read)["properties"]
        assert (
            properties["path"]["description"]
            == "The file to read, relative to the root."
        )
        assert properties["lines"]["description"] == "How many lines."

    def test_precedence(self) -> None:
        @Tool(name="p", description="P", params={"a": "From params.", "b": "B params."})
        async def p(
            a: Annotated[str, Desc("From Desc.")], b: str, c: str
        ) -> ToolResult[str]:
            """P.

            Args:
                a: From Args.
                b: From Args.
                c: From Args.
            """
            return ToolResult.ok("ok")

        properties = _parameters(p)["properties"]
        assert properties["a"]["description"] == "From Desc."
        assert properties["b"]["description"] == "B params."
        assert properties["c"]["description"] == "From Args."

    def test_lift_parses_the_google_shapes(self) -> None:
        doc = "Summary.\n\nArgs:\n    x: One.\n    y (str, optional): Two\n        more.\n\nRaises:\n    ValueError: never lifted.\n"
        assert lift_args_docstring(doc) == {"x": "One.", "y": "Two more."}
        assert lift_args_docstring(None) == {}
        assert lift_args_docstring("No section.") == {}

    def test_every_builtin_parameter_is_described(self) -> None:
        from neosian._foundation.tools.builtin.todo import update_todo

        properties = _parameters(update_todo)["properties"]
        assert "content" in properties["todos"]["description"]
        assert "status" in properties["todos"]["description"]


@pytest.mark.unit
class TestValidation:
    """TG-3: the body receives what the signature promises, or the model
    receives a repairable failure."""

    def test_validated_values_reach_the_body(self) -> None:
        @Tool(name="ship", description="Ship")
        async def ship(to: Address, at: datetime, qty: int = 1) -> ToolResult[str]:
            return ToolResult.ok("ok")

        validated = validate_arguments(
            _model(ship),
            {"to": {"street": "s", "zip": "12345"}, "at": "2026-09-05T00:00:00Z"},
        )
        assert validated == {
            "to": Address(street="s", zip="12345"),
            "at": datetime(2026, 9, 5, tzinfo=UTC),
            "qty": 1,
        }

    def test_lax_coercion_repairs_a_stringly_integer(self) -> None:
        @Tool(name="n", description="N")
        async def n(count: int) -> ToolResult[str]:
            return ToolResult.ok("ok")

        assert validate_arguments(_model(n), {"count": "7"}) == {"count": 7}

    def test_the_failure_names_every_field_without_the_footer(self) -> None:
        @Tool(name="ship", description="Ship")
        async def ship(to: Address, qty: Annotated[int, Min(1)]) -> ToolResult[str]:
            return ToolResult.ok("ok")

        with pytest.raises(ValidationError) as excinfo:
            validate_arguments(
                _model(ship), {"to": {"street": "s", "zip": "1"}, "qty": 0, "x": 1}
            )
        result = rejection("ship", excinfo.value)
        assert result.success is False
        assert result.code == "tool_invalid_arguments"
        assert result.error is not None
        assert result.error.startswith("Invalid arguments for tool 'ship': ")
        assert "to.zip: String should match pattern" in result.error
        assert "qty: Input should be greater than or equal to 1" in result.error
        assert "x: Extra inputs are not permitted" in result.error
        assert "errors.pydantic.dev" not in result.error

    @pytest.mark.asyncio
    async def test_the_agent_path_validates_then_calls(self) -> None:
        received: list[Any] = []

        @Tool(name="ship", description="Ship")
        async def ship(to: Address, qty: int = 1) -> ToolResult[str]:
            received.append((to, qty))
            return ToolResult.ok("shipped")

        agent = _agent(ship)
        bad = ToolCall(
            id=ToolCallId("c1"), name=ToolName("ship"), arguments={"to": {"street": 1}}
        )
        result = await execute_tool(agent, bad)
        assert result.success is False
        assert result.code == "tool_invalid_arguments"
        assert received == []

        good = ToolCall(
            id=ToolCallId("c2"),
            name=ToolName("ship"),
            arguments={"to": {"street": "s", "zip": "12345"}, "qty": "2"},
        )
        result = await execute_tool(agent, good)
        assert result.success is True
        assert received == [(Address(street="s", zip="12345"), 2)]

    @pytest.mark.asyncio
    async def test_an_attached_definition_only_binds(self) -> None:
        """A library-attached definition (an MCP server's) has no model:
        the server validates, the core binds."""

        async def bridged(**arguments: Any) -> ToolResult[Any]:
            return ToolResult.ok(arguments)

        attach_tool_metadata(
            bridged,
            ToolDefinition(
                name=ToolName("remote"),
                description="Remote",
                parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            ),
        )
        result = await execute_tool(
            _agent(bridged),
            ToolCall(id=ToolCallId("c"), name=ToolName("remote"), arguments={"n": "x"}),
        )
        assert result.success is True
        assert result.data == {"n": "x"}


@pytest.mark.unit
class TestStrictSchema:
    """The strict-mode shape the OpenAI wire and Cerebras send (TG-9)."""

    def test_the_rules(self) -> None:
        @Tool(name="s", description="S")
        async def s(
            to: Annotated[Address, Desc("Where.")], n: int | None = None, k: int = 3
        ) -> ToolResult[str]:
            return ToolResult.ok("ok")

        original = _parameters(s)
        strict = strict_schema(original)
        assert strict["required"] == ["to", "n", "k"]
        assert "default" not in strict["properties"]["n"]
        assert strict["properties"]["k"]["default"] == 3
        assert "$ref" not in strict["properties"]["to"]
        assert strict["properties"]["to"]["description"] == "Where."
        assert strict["properties"]["to"]["required"] == ["street", "zip"]
        assert strict["properties"]["to"]["additionalProperties"] is False
        assert strict["$defs"]["Address"]["required"] == ["street", "zip"]
        # the input is untouched
        assert original["required"] == ["to"]
        assert original["properties"]["to"] == {
            "$ref": "#/$defs/Address",
            "description": "Where.",
        }
