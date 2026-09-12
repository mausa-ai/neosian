"""Tools and output schemas (`anthropic_tools.py`): constraints, strict, choice, native types."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.anthropic_tools import (
    convert_tool_choice,
    strip_unsupported_constraints,
)
from neosian._foundation.llm.base import (
    Message,
    ToolDefinition,
)
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ToolChoice,
    ToolName,
)
from tests.unit.llm.anthropic.mocks import mock_complete, sdk, stub_stream
from tests.unit.llm.sdk_specs import ANTHROPIC as SPEC


@pytest.mark.unit
class TestStripUnsupportedConstraints:
    """Tests for _strip_unsupported_constraints schema sanitizer."""

    def test_strips_integer_constraints(self) -> None:
        """minimum/maximum should be removed from integer properties."""
        schema = {
            "type": "object",
            "properties": {
                "fontsize": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Font size",
                },
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        prop = result["properties"]["fontsize"]
        assert "minimum" not in prop
        assert "maximum" not in prop
        assert prop["description"] == "Font size"

    def test_strips_number_constraints(self) -> None:
        """minimum/maximum should be removed from number properties."""
        schema = {
            "type": "object",
            "properties": {
                "width": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 5.0,
                },
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minimum" not in result["properties"]["width"]
        assert "maximum" not in result["properties"]["width"]

    def test_strips_exclusive_constraints(self) -> None:
        """exclusiveMinimum/exclusiveMaximum should also be removed."""
        schema = {
            "type": "object",
            "properties": {
                "val": {
                    "type": "integer",
                    "exclusiveMinimum": 0,
                    "exclusiveMaximum": 100,
                },
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "exclusiveMinimum" not in result["properties"]["val"]
        assert "exclusiveMaximum" not in result["properties"]["val"]

    def test_strips_string_length_constraints_preserves_pattern(self) -> None:
        """minLength/maxLength stripped; pattern preserved (per strict-mode docs)."""
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": "^[a-z]+$",
                },
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        prop = result["properties"]["name"]
        assert "minLength" not in prop
        assert "maxLength" not in prop
        assert prop["pattern"] == "^[a-z]+$"

    def test_recurses_into_defs(self) -> None:
        """Should strip constraints in $defs."""
        schema = {
            "type": "object",
            "$defs": {
                "Size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "properties": {},
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minimum" not in result["$defs"]["Size"]
        assert "maximum" not in result["$defs"]["Size"]

    def test_recurses_into_anyof(self) -> None:
        """Should strip constraints inside anyOf variants."""
        schema = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "minimum": 0},
                    },
                },
            ],
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minimum" not in result["anyOf"][0]["properties"]["count"]

    def test_does_not_mutate_original(self) -> None:
        """Original schema dict should not be modified."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        }
        strip_unsupported_constraints(schema, strict=True)
        assert schema["properties"]["x"]["minimum"] == 1
        assert schema["properties"]["x"]["maximum"] == 10

    def test_convert_tools_strips_constraints(self, client: AnthropicClient) -> None:
        """Strict tools get full strict-mode stripping via _convert_tools."""
        tool = ToolDefinition(
            name=ToolName("caption"),
            description="Add captions",
            parameters={
                "type": "object",
                "properties": {
                    "fontsize": {"type": "integer", "minimum": 1, "maximum": 20},
                    "label": {"type": "string", "minLength": 1, "maxLength": 50},
                },
            },
            strict=True,
        )
        converted = client._convert_tools([tool])
        props = converted[0]["input_schema"]["properties"]
        assert "minimum" not in props["fontsize"]
        assert "maximum" not in props["fontsize"]
        # String length constraints also stripped under strict mode
        assert "minLength" not in props["label"]
        assert "maxLength" not in props["label"]
        # Original not mutated
        assert tool.parameters["properties"]["fontsize"]["minimum"] == 1
        assert tool.parameters["properties"]["label"]["minLength"] == 1

    def test_strips_minlength_on_strings(self) -> None:
        """minLength on string properties is stripped."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minLength" not in result["properties"]["name"]

    def test_strips_maxlength_on_strings(self) -> None:
        """maxLength on string properties is stripped."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 100},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "maxLength" not in result["properties"]["name"]

    def test_strips_multipleof_on_numbers(self) -> None:
        """multipleOf is stripped from integer/number properties."""
        schema = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "multipleOf": 5},
                "f": {"type": "number", "multipleOf": 0.25},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "multipleOf" not in result["properties"]["n"]
        assert "multipleOf" not in result["properties"]["f"]

    def test_strips_minitems_above_one(self) -> None:
        """minItems > 1 is stripped from array properties."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "minItems": 3, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "minItems" not in result["properties"]["tags"]

    def test_keeps_minitems_zero_or_one(self) -> None:
        """minItems of 0 or 1 is kept (allowed under strict mode)."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "array", "minItems": 0, "items": {"type": "string"}},
                "b": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert result["properties"]["a"]["minItems"] == 0
        assert result["properties"]["b"]["minItems"] == 1

    def test_strips_maxitems_above_one(self) -> None:
        """maxItems > 1 is stripped from array properties."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert "maxItems" not in result["properties"]["tags"]

    def test_keeps_maxitems_zero_or_one(self) -> None:
        """maxItems of 0 or 1 is kept (allowed under strict mode)."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "array", "maxItems": 0, "items": {"type": "string"}},
                "b": {"type": "array", "maxItems": 1, "items": {"type": "string"}},
            },
        }
        result = strip_unsupported_constraints(schema, strict=True)
        assert result["properties"]["a"]["maxItems"] == 0
        assert result["properties"]["b"]["maxItems"] == 1

    def test_strict_false_preserves_minlength(self) -> None:
        """strict=False keeps minLength on string schemas."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert result["properties"]["name"]["minLength"] == 1

    def test_strict_false_preserves_multipleof(self) -> None:
        """strict=False keeps multipleOf on integer/number schemas."""
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer", "multipleOf": 5}},
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert result["properties"]["n"]["multipleOf"] == 5

    def test_strict_false_still_strips_numeric_minimum(self) -> None:
        """strict=False still strips minimum/maximum (Anthropic always rejects)."""
        schema = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        }
        result = strip_unsupported_constraints(schema, strict=False)
        assert "minimum" not in result["properties"]["n"]
        assert "maximum" not in result["properties"]["n"]


@pytest.mark.unit
class TestAnthropicStrictToolUse:
    """Tests for strict tool use payload shape."""

    def test_strict_tool_emits_strict_flag(self, client: AnthropicClient) -> None:
        """A tool with strict=True declares strict: true on the wire."""
        tool = ToolDefinition(
            name=ToolName("get_weather"),
            description="Get the weather",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["strict"] is True

    def test_strict_tool_sets_additional_properties_false(
        self, client: AnthropicClient
    ) -> None:
        """Strict tool's object input_schema gets additionalProperties: false set."""
        tool = ToolDefinition(
            name=ToolName("get_weather"),
            description="Get the weather",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is False

    def test_strict_tool_preserves_existing_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """A strict tool that already declares additionalProperties is not overwritten."""
        tool = ToolDefinition(
            name=ToolName("passthrough"),
            description="Passthrough tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": True,
            },
            strict=True,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is True

    def test_non_strict_tool_omits_strict_key(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Non-strict tools must not have a `strict` key on the wire."""
        # sample_tool fixture has strict=False (default)
        converted = client._convert_tools([sample_tool])
        assert "strict" not in converted[0]

    def test_non_strict_tool_omits_additional_properties_injection(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Non-strict tools must not get auto-injected additionalProperties."""
        converted = client._convert_tools([sample_tool])
        assert "additionalProperties" not in converted[0]["input_schema"]

    def test_non_strict_tool_preserves_caller_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """If a caller declared additionalProperties on a non-strict tool, keep it."""
        tool = ToolDefinition(
            name=ToolName("passthrough"),
            description="Passthrough tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": True,
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        assert converted[0]["input_schema"]["additionalProperties"] is True

    def test_mixed_strict_and_non_strict_tools_in_one_call(
        self, client: AnthropicClient
    ) -> None:
        """Each tool is treated independently when strict differs across the list."""
        strict_tool = ToolDefinition(
            name=ToolName("strict_one"),
            description="Strict tool",
            parameters={"type": "object", "properties": {}},
            strict=True,
        )
        non_strict_tool = ToolDefinition(
            name=ToolName("lax_one"),
            description="Non-strict tool",
            parameters={"type": "object", "properties": {}},
            strict=False,
        )
        converted = client._convert_tools([strict_tool, non_strict_tool])

        assert converted[0]["strict"] is True
        assert converted[0]["input_schema"]["additionalProperties"] is False

        assert "strict" not in converted[1]
        assert "additionalProperties" not in converted[1]["input_schema"]

    def test_non_strict_tool_preserves_string_length_constraints(
        self, client: AnthropicClient
    ) -> None:
        """Non-strict tools keep minLength/maxLength (only strict mode strips them)."""
        tool = ToolDefinition(
            name=ToolName("caption"),
            description="Add captions",
            parameters={
                "type": "object",
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 50},
                },
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        label = converted[0]["input_schema"]["properties"]["label"]
        assert label["minLength"] == 1
        assert label["maxLength"] == 50

    def test_non_strict_tool_strips_only_always_unsupported_numeric(
        self, client: AnthropicClient
    ) -> None:
        """Non-strict tools still drop minimum/maximum (always rejected by Anthropic)
        but keep multipleOf (rejected only under strict mode)."""
        tool = ToolDefinition(
            name=ToolName("size"),
            description="Size in pixels",
            parameters={
                "type": "object",
                "properties": {
                    "px": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "multipleOf": 5,
                    },
                },
            },
            strict=False,
        )
        converted = client._convert_tools([tool])
        px = converted[0]["input_schema"]["properties"]["px"]
        assert "minimum" not in px
        assert "maximum" not in px
        assert px["multipleOf"] == 5


@pytest.mark.unit
class TestAnthropicToolChoice:
    """The four choices are Anthropic's four types, and a choice only ever
    rides beside a tool list (NC9 #224)."""

    @pytest.mark.parametrize(
        ("choice", "expected"),
        [
            (ToolChoice.auto(), {"type": "auto"}),
            (ToolChoice.required(), {"type": "any"}),
            (ToolChoice.none(), {"type": "none"}),
            (ToolChoice.tool("get_weather"), {"type": "tool", "name": "get_weather"}),
            (
                ToolChoice.required(parallel=False),
                {"type": "any", "disable_parallel_tool_use": True},
            ),
        ],
        ids=["auto", "required", "none", "tool", "serial"],
    )
    def test_the_wire_shapes(
        self, choice: ToolChoice, expected: dict[str, Any]
    ) -> None:
        assert convert_tool_choice(choice) == expected

    def test_none_has_no_parallel_knob(self) -> None:
        """Nothing is called, so nothing can be parallel."""
        assert "disable_parallel_tool_use" not in convert_tool_choice(ToolChoice.none())

    @pytest.mark.asyncio
    async def test_the_choice_reaches_the_body_beside_the_tools(
        self,
        client: AnthropicClient,
        sample_messages: list[Message],
        sample_tool: ToolDefinition,
    ) -> None:
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"
        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[sample_tool],
            tool_choice=ToolChoice.tool("get_weather"),
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "get_weather"}

    @pytest.mark.asyncio
    async def test_without_tools_no_choice_is_sent(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """A tool_choice with no tools is a 400 on the wire."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="hi")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"
        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tool_choice=ToolChoice.required(),
        )

        assert "tool_choice" not in sdk(client).messages.stream.call_args.kwargs


@pytest.mark.unit
class TestAnthropicStructuredOutput:
    """Tests for GA structured-output payload shape (output_config.format)."""

    @pytest.mark.asyncio
    async def test_uses_output_config_format(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """response_format is sent under output_config.format on the GA endpoint."""
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Out(BaseModel):
            answer: str

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text='{"answer":"hi"}')
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-sonnet-5"

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            response_format=ResponseFormat(schema=Out),
        )

        sdk(client).messages.stream.assert_called_once()
        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        # GA shape: format lives under output_config["format"]
        assert "output_config" in call_kwargs
        format_spec = call_kwargs["output_config"]["format"]
        assert format_spec["type"] == "json_schema"
        assert "schema" in format_spec
        # Beta-era keys must be absent
        assert "output_format" not in call_kwargs
        assert "betas" not in call_kwargs

    @pytest.mark.asyncio
    async def test_output_config_format_merges_with_reasoning_effort(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """When reasoning_effort and response_format both set, output_config holds both."""
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Out(BaseModel):
            answer: str

        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text='{"answer":"hi"}')
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=5, output_tokens=3
        )
        mock_response.model = "claude-opus-5"

        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_OPUS_5,
            response_format=ResponseFormat(schema=Out),
            reasoning_effort=ReasoningEffort.HIGH,
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        output_config = call_kwargs["output_config"]
        assert output_config["effort"] == "high"
        assert output_config["format"]["type"] == "json_schema"


@pytest.mark.unit
class TestAnthropicSchemaAdditionalProperties:
    """Every object in an output-format schema must carry additionalProperties.

    Anthropic rejects object schemas without it — including nested models
    under $defs, and regardless of strict mode.
    """

    def _nested_format(self, strict: bool = True) -> object:
        from pydantic import BaseModel

        from neosian._foundation.shared.types import ResponseFormat

        class Question(BaseModel):
            question: str
            options: list[str]

        class Quiz(BaseModel):
            questions: list[Question]

        return ResponseFormat(schema=Quiz, strict=strict)

    def test_nested_model_defs_carry_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """Regression: $defs objects reached the API without the flag (400)."""
        payload = client._convert_response_format(self._nested_format())  # type: ignore[arg-type]
        schema = payload["schema"]

        assert schema["additionalProperties"] is False  # type: ignore[index]
        assert schema["$defs"]["Question"]["additionalProperties"] is False  # type: ignore[index]

    def test_non_strict_schema_still_carries_additional_properties(
        self, client: AnthropicClient
    ) -> None:
        """Regression: the root patch used to be gated on strict, so
        strict=False sent an object schema without the flag and 400'd."""
        payload = client._convert_response_format(
            self._nested_format(strict=False)  # type: ignore[arg-type]
        )
        schema = payload["schema"]

        assert schema["additionalProperties"] is False  # type: ignore[index]
        assert schema["$defs"]["Question"]["additionalProperties"] is False  # type: ignore[index]


@pytest.mark.unit
class TestNativeToolType:
    """Tools marked native_type ride the schema-less native declaration."""

    def _native(self) -> ToolDefinition:
        return ToolDefinition(
            name=ToolName("memory"),
            description="ignored on the wire under native transport",
            parameters={"type": "object", "properties": {}},
            native_type="memory_20250818",
        )

    def test_native_wire_shape(self, client: AnthropicClient) -> None:
        """A native entry is exactly {type, name} plus the cache breakpoint."""
        converted = client._convert_tools([self._native()])
        assert converted == [
            {
                "type": "memory_20250818",
                "name": "memory",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_mixed_list_keeps_function_schema_beside_native(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        converted = client._convert_tools([sample_tool, self._native()])
        assert converted[0]["name"] == "get_weather"
        assert converted[0]["description"] == "Get the weather for a location"
        assert "input_schema" in converted[0]
        assert "cache_control" not in converted[0]
        assert converted[1] == {
            "type": "memory_20250818",
            "name": "memory",
            "cache_control": {"type": "ephemeral"},
        }

    def test_unmarked_definition_is_unchanged(
        self, client: AnthropicClient, sample_tool: ToolDefinition
    ) -> None:
        """Regression: native_type=None produces today's exact shape."""
        converted = client._convert_tools([sample_tool])
        assert converted == [
            {
                "name": "get_weather",
                "description": "Get the weather for a location",
                "input_schema": sample_tool.parameters,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @pytest.mark.asyncio
    async def test_complete_sends_native_entry_without_betas(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        """The native memory tool is GA — no beta header, no schema."""
        mock_response = MagicMock(spec_set=SPEC["message"])
        mock_response.content = [
            MagicMock(spec_set=SPEC["text"], type="text", text="ok")
        ]
        mock_response.usage = MagicMock(
            spec=SPEC["usage"], input_tokens=1, output_tokens=1
        )
        mock_response.model = "claude-sonnet-5"
        mock_complete(client, mock_response)

        await client.complete(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[self._native()],
        )

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tools"] == [
            {
                "type": "memory_20250818",
                "name": "memory",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert "betas" not in call_kwargs

    @pytest.mark.asyncio
    async def test_stream_sends_the_same_native_entry(
        self, client: AnthropicClient, sample_messages: list[Message]
    ) -> None:
        mock_delta = MagicMock(spec_set=SPEC["content_block_delta"])
        mock_delta.type = "content_block_delta"
        mock_delta.delta = MagicMock(
            spec=SPEC["text_delta"], type="text_delta", text="ok"
        )
        mock_stop = MagicMock(spec_set=SPEC["message_stop"])
        mock_stop.type = "message_stop"

        async def mock_stream_events() -> AsyncIterator[Any]:
            yield mock_delta
            yield mock_stop

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda _: mock_stream_events()
        stub_stream(client, mock_stream)

        async for _ in client.stream(
            messages=sample_messages,
            model=Model.CLAUDE_SONNET_5,
            tools=[self._native()],
        ):
            pass

        call_kwargs = sdk(client).messages.stream.call_args.kwargs
        assert call_kwargs["tools"][0] == {
            "type": "memory_20250818",
            "name": "memory",
            "cache_control": {"type": "ephemeral"},
        }
        assert "betas" not in call_kwargs
