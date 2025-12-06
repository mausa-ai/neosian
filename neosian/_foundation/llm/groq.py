"""Groq LLM client implementation."""

import json
from collections.abc import AsyncIterator

from groq import AsyncGroq, BadRequestError
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
from neosian._foundation.shared.constants import LLMDefaults
from neosian._foundation.shared.exceptions import ToolCallGenerationError
from neosian._foundation.shared.types import ModelId, ToolCallId, ToolName


class GroqClient(BaseLLMClient):
    """Groq LLM client.

    Uses the Groq SDK for fast inference. Includes automatic retry with
    lower temperature when tool call generation fails.
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
        temperature: float | None = None,
    ) -> CompletionResponse:
        """Send a completion request to Groq.

        Automatically retries with lower temperature if tool call generation
        fails. After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses default.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            ToolCallGenerationError: If tool call generation fails after retries.
        """
        groq_messages = self._convert_messages(messages)
        groq_tools = self._convert_tools(tools) if tools else None

        # Use provided temperature or default
        current_temp = (
            temperature if temperature is not None else LLMDefaults.TEMPERATURE
        )

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=groq_messages,
                    tools=groq_tools,
                    temperature=current_temp,
                )
                return self._parse_response(response)

            except BadRequestError as e:
                # Check if it's a tool_use_failed error
                if self._is_tool_call_error(e) and tools is not None:
                    # If we have retries left, try with lower temperature
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        current_temp = LLMDefaults.RETRY_TEMPERATURE
                        continue
                    # Max retries exceeded
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
                # Not a tool call error, re-raise
                raise

        # Should not reach here, but satisfy type checker
        raise ToolCallGenerationError(retries=LLMDefaults.MAX_TOOL_CALL_RETRIES)

    def _is_tool_call_error(self, error: BadRequestError) -> bool:
        """Check if the error is a tool call generation failure.

        Args:
            error: The BadRequestError to check.

        Returns:
            True if it's a tool_use_failed error.
        """
        if error.body and isinstance(error.body, dict):
            err = error.body.get("error", {})
            if isinstance(err, dict):
                return err.get("code") == "tool_use_failed"
        return False

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse Groq response into CompletionResponse.

        Args:
            response: Raw response from Groq API.

        Returns:
            Parsed CompletionResponse.
        """
        # Type ignore needed because groq SDK types are complex
        choice = response.choices[0]  # type: ignore[attr-defined]
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
                input_tokens=response.usage.prompt_tokens if response.usage else 0,  # type: ignore[attr-defined]
                output_tokens=response.usage.completion_tokens if response.usage else 0,  # type: ignore[attr-defined]
            ),
            model=ModelId(response.model),  # type: ignore[attr-defined]
        )

    async def stream(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from Groq.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-2.0). None uses default.

        Yields:
            StreamChunk objects as they arrive.
        """
        groq_messages = self._convert_messages(messages)
        groq_tools = self._convert_tools(tools) if tools else None
        temp = temperature if temperature is not None else LLMDefaults.TEMPERATURE

        stream = await self._client.chat.completions.create(
            model=model,
            messages=groq_messages,
            tools=groq_tools,
            temperature=temp,
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
