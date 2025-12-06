"""Groq LLM client implementation."""

import json
from collections.abc import AsyncIterator

from groq import AsyncGroq
from groq.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from neosian._foundation.llm.base import (
    BaseLLMClient,
    CompletionResponse,
    Message,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.shared.types import ModelId, ToolCallId, ToolName


class GroqClient(BaseLLMClient):
    """Groq LLM client.

    Uses the Groq SDK for fast inference.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize the Groq client.

        Args:
            api_key: Groq API key. Required, no implicit env var reading.
        """
        self._client = AsyncGroq(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
    ) -> CompletionResponse:
        """Send a completion request to Groq.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.

        Returns:
            CompletionResponse with the model's response.
        """
        groq_messages = self._convert_messages(messages)
        groq_tools = self._convert_tools(tools) if tools else None

        response = await self._client.chat.completions.create(
            model=model,
            messages=groq_messages,
            tools=groq_tools,
        )

        choice = response.choices[0]
        response_message = choice.message

        # Convert tool calls if present
        tool_calls: list[ToolCall] = []
        if response_message.tool_calls:
            for tc in response_message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(tc.id),
                        name=ToolName(tc.function.name),
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=response_message.content,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            ),
            model=ModelId(response.model),
        )

    async def stream(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from Groq.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.

        Yields:
            StreamChunk objects as they arrive.
        """
        groq_messages = self._convert_messages(messages)
        groq_tools = self._convert_tools(tools) if tools else None

        stream = await self._client.chat.completions.create(
            model=model,
            messages=groq_messages,
            tools=groq_tools,
            stream=True,
        )

        # Track tool calls being built across chunks
        tool_call_builders: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Handle content
            content = delta.content if delta.content else None

            # Handle tool calls (streamed in parts)
            tool_calls: list[ToolCall] = []
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_builders:
                        tool_call_builders[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }

                    if tc.id:
                        tool_call_builders[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_builders[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_builders[idx][
                                "arguments"
                            ] += tc.function.arguments

            # On finish, yield completed tool calls
            finish_reason = choice.finish_reason
            if finish_reason == "tool_calls" and tool_call_builders:
                for builder in tool_call_builders.values():
                    tool_calls.append(
                        ToolCall(
                            id=ToolCallId(builder["id"]),
                            name=ToolName(builder["name"]),
                            arguments=json.loads(builder["arguments"]),
                        )
                    )

            yield StreamChunk(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )

    def _convert_messages(
        self, messages: list[Message]
    ) -> list[ChatCompletionMessageParam]:
        """Convert internal messages to Groq format."""
        result: list[ChatCompletionMessageParam] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                result.append({"role": "system", "content": msg.content or ""})
            elif msg.role == Role.USER:
                result.append({"role": "user", "content": msg.content or ""})
            elif msg.role == Role.ASSISTANT:
                if msg.tool_calls:
                    assistant_msg: ChatCompletionAssistantMessageParam = {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                    result.append(assistant_msg)
                else:
                    result.append({"role": "assistant", "content": msg.content})
            elif msg.role == Role.TOOL:
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content or "",
                        "tool_call_id": msg.tool_call_id or "",
                    }
                )

        return result

    def _convert_tools(
        self, tools: list[ToolDefinition]
    ) -> list[ChatCompletionToolParam]:
        """Convert internal tool definitions to Groq format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
