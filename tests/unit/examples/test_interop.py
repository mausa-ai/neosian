"""The interop examples, keyless (NC3): each framework's own scripted model
drives the example, the framework sees the exported definition, and the
document lands in the FileStore root through the tool itself."""

# ruff: noqa: ARG002  (the scripted Model carries the SDK's full signature)

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from agents import (
    AgentOutputSchemaBase,
    FunctionTool,
    Handoff,
    Model,
    ModelResponse,
    ModelSettings,
    ModelTracing,
    Tool,
    TResponseInputItem,
    Usage,
)
from agents.items import TResponseStreamEvent
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponsePromptParam,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse as PydanticResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from examples import interop_openai_agents, interop_pydantic_ai
from neosian import FileStore, MemoryConfig, create_memory_tool, tool_definition

_ARGUMENTS = {"command": "create", "path": "/user/tea", "content": "takes tea"}
_COMMANDS = ["view", "create", "str_replace", "insert", "delete", "rename"]


def _function_call_outputs(items: str | list[TResponseInputItem]) -> list[str]:
    """The tool outputs the SDK fed back to the model, in order."""
    if isinstance(items, str):
        return []
    rows = [cast(Mapping[str, Any], item) for item in items if isinstance(item, dict)]
    return [
        str(row["output"]) for row in rows if row.get("type") == "function_call_output"
    ]


def _expected_schema(root: Path) -> dict[str, Any]:
    config = MemoryConfig(store=FileStore(root), mounts=interop_pydantic_ai.MOUNTS)
    return tool_definition(create_memory_tool(config)).parameters


async def _assert_written(root: Path, actor: str) -> None:
    document = await FileStore(root).read("user:demo", "tea")
    assert document is not None
    assert (document.content, document.version, document.actor) == (
        "takes tea",
        1,
        actor,
    )


@pytest.mark.unit
async def test_pydantic_ai_example_writes_through_the_exported_definition(
    tmp_path: Path,
) -> None:
    seen: dict[str, Any] = {}

    def scripted(messages: list[ModelMessage], info: AgentInfo) -> PydanticResponse:
        seen["tools"], seen["instructions"] = info.function_tools, info.instructions
        last = messages[-1]
        if isinstance(last, ModelRequest):
            returns = [part for part in last.parts if isinstance(part, ToolReturnPart)]
            if returns:
                seen["returned"] = returns[0].content
                return PydanticResponse(parts=[TextPart(content="remembered")])
        return PydanticResponse(
            parts=[ToolCallPart(tool_name="memory", args=_ARGUMENTS)]
        )

    root = tmp_path / "memory"
    answer = await interop_pydantic_ai.run(
        FunctionModel(scripted), "remember", root=root
    )

    assert answer == "remembered"
    (definition,) = seen["tools"]
    assert definition.name == "memory"
    assert definition.parameters_json_schema == _expected_schema(root)
    assert (
        definition.parameters_json_schema["properties"]["command"]["enum"] == _COMMANDS
    )
    assert definition.parameters_json_schema["required"] == ["command"]
    assert "## Memory" in seen["instructions"]
    assert "## /user" in seen["instructions"]
    assert seen["returned"] == '{"success": true, "data": "Created /user/tea (v1)"}'
    await _assert_written(root, interop_pydantic_ai.ACTOR)


class ScriptedModel(Model):
    """A `Model` that calls `memory` once, then answers."""

    def __init__(self) -> None:
        self.calls: list[
            tuple[str | None, list[Tool], str | list[TResponseInputItem]]
        ] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self.calls.append((system_instructions, tools, input))
        if _function_call_outputs(input):
            text = ResponseOutputText(
                type="output_text", text="remembered", annotations=[]
            )
            message = ResponseOutputMessage(
                id="msg_1",
                type="message",
                role="assistant",
                status="completed",
                content=[text],
            )
            return ModelResponse(output=[message], usage=Usage(), response_id=None)
        call = ResponseFunctionToolCall(
            id="fc_1",
            call_id="call_1",
            type="function_call",
            status="completed",
            name="memory",
            arguments=json.dumps(_ARGUMENTS),
        )
        return ModelResponse(output=[call], usage=Usage(), response_id=None)

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("the example runs blocking")


@pytest.mark.unit
async def test_openai_agents_example_writes_through_the_exported_definition(
    tmp_path: Path,
) -> None:
    model = ScriptedModel()
    root = tmp_path / "memory"

    answer = await interop_openai_agents.run(model, "remember", root=root)

    assert answer == "remembered"
    instructions, tools, _ = model.calls[0]
    (tool,) = tools
    assert isinstance(tool, FunctionTool)
    assert tool.name == "memory"
    assert tool.params_json_schema == _expected_schema(root)  # strict off: untouched
    assert instructions is not None
    assert "## Memory" in instructions
    assert "## /user" in instructions
    _, _, second_input = model.calls[1]
    assert _function_call_outputs(second_input) == [
        '{"success": true, "data": "Created /user/tea (v1)"}'
    ]
    await _assert_written(root, interop_openai_agents.ACTOR)
