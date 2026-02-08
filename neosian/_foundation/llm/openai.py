"""OpenAI LLM client implementation."""

import json
from collections.abc import AsyncIterator
from typing import cast

from openai import NOT_GIVEN, AsyncOpenAI, BadRequestError
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionStreamOptionsParam,
    ChatCompletionToolParam,
)
from openai.types.chat.completion_create_params import (
    ResponseFormat as OpenAIResponseFormat,
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
from neosian._foundation.shared.constants import ErrorMessages, LLMDefaults
from neosian._foundation.shared.exceptions import (
    ToolCallGenerationError,
    UnsupportedParameterError,
)
from neosian._foundation.shared.serialization import safe_json_dumps
from neosian._foundation.shared.types import (
    Model,
    ReasoningEffort,
    ResponseFormat,
    ToolCallId,
    ToolName,
)


class OpenAIClient(BaseLLMClient):
    """OpenAI LLM client.

    Uses the OpenAI SDK. Includes automatic retry with lower temperature
    when tool call generation fails.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize the OpenAI client.

        Args:
            api_key: OpenAI API key. Required, no implicit env var reading.
        """
        self._client = AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        response_format: ResponseFormat | None = None,
        reasoning_effort: ReasoningEffort | None = None,  # noqa: ARG002
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
    ) -> CompletionResponse:
        """Send a completion request to OpenAI.

        Automatically retries with lower temperature if tool call generation
        fails. After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Not supported for GPT-5 models. Raises error if provided.
            response_format: Optional structured output configuration.
            reasoning_effort: Ignored (Groq GPT-OSS only).

        Returns:
            CompletionResponse with the model's response.

        Raises:
            UnsupportedParameterError: If temperature is provided.
            ToolCallGenerationError: If tool call generation fails after retries.
        """
        if temperature is not None:
            raise UnsupportedParameterError(
                ErrorMessages.OPENAI_TEMPERATURE_NOT_SUPPORTED
            )

        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None
        openai_response_format: OpenAIResponseFormat | None = (
            self._convert_response_format(response_format) if response_format else None
        )

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model.value,
                    messages=openai_messages,
                    tools=openai_tools if openai_tools else NOT_GIVEN,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    response_format=openai_response_format if openai_response_format else NOT_GIVEN,  # type: ignore[arg-type]
                )
                return self._parse_response(response)

            except BadRequestError as e:
                # Check if it's a tool call error
                if self._is_tool_call_error(e) and tools is not None:
                    # If we have retries left, retry
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
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
            True if it's a tool call related error.
        """
        # OpenAI returns different error codes/messages for tool failures
        if error.body and isinstance(error.body, dict):
            err = error.body.get("error", {})
            if isinstance(err, dict):
                code = err.get("code", "")
                message = err.get("message", "")
                # Check for various tool-related error indicators
                return (
                    code == "invalid_tool_call"
                    or "tool" in message.lower()
                    or "function" in message.lower()
                )
        return False

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse OpenAI response into CompletionResponse.

        Args:
            response: Raw response from OpenAI API.

        Returns:
            Parsed CompletionResponse.
        """
        # Type ignore needed because openai SDK types are complex
        choice = response.choices[0]  # type: ignore[attr-defined]
        response_message = choice.message

        # Convert tool calls if present
        tool_calls: list[ToolCall] = []
        if response_message.tool_calls:
            for tc in response_message.tool_calls:
                # Normalize empty/missing arguments to {}
                args_str = tc.function.arguments or "{}"
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(tc.id),
                        name=ToolName(tc.function.name),
                        arguments=json.loads(args_str),
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
            model=response.model,  # type: ignore[attr-defined]
        )

    async def stream(
        self,
        messages: list[Message],
        model: Model,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,  # noqa: ARG002
        max_tokens: int = LLMDefaults.MAX_OUTPUT_TOKENS,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from OpenAI.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Not supported for GPT-5 models. Raises error if provided.
            reasoning_effort: Ignored (Groq GPT-OSS only).

        Yields:
            StreamChunk objects as they arrive.

        Raises:
            UnsupportedParameterError: If temperature is provided.
        """
        if temperature is not None:
            raise UnsupportedParameterError(
                ErrorMessages.OPENAI_TEMPERATURE_NOT_SUPPORTED
            )

        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        stream_opts: ChatCompletionStreamOptionsParam = {"include_usage": True}
        stream = await self._client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=openai_tools if openai_tools else NOT_GIVEN,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            stream=True,
            stream_options=stream_opts,
        )

        # Track tool calls being built across chunks
        tool_call_builders: dict[int, dict[str, str]] = {}

        async for chunk in stream:  # type: ignore[union-attr]
            # Handle usage-only chunk (comes after finish_reason)
            if not chunk.choices and chunk.usage:
                yield StreamChunk(
                    usage=Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    ),
                )
                continue

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
                    # Normalize empty arguments to {}
                    args_str = builder["arguments"] or "{}"
                    tool_calls.append(
                        ToolCall(
                            id=ToolCallId(builder["id"]),
                            name=ToolName(builder["name"]),
                            arguments=json.loads(args_str),
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
        """Convert internal messages to OpenAI format."""
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
                                    "arguments": safe_json_dumps(
                                        tc.arguments, "tool_call.arguments"
                                    ),
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
        """Convert internal tool definitions to OpenAI format."""
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

    def _convert_response_format(
        self, response_format: ResponseFormat
    ) -> OpenAIResponseFormat:
        """Convert internal ResponseFormat to OpenAI response_format.

        Args:
            response_format: Internal ResponseFormat configuration.

        Returns:
            OpenAI-compatible response_format TypedDict.
        """
        from neosian._foundation.shared.schema import get_json_schema, get_schema_name

        schema = get_json_schema(response_format.schema)
        # OpenAI requires additionalProperties: false for strict mode
        schema["additionalProperties"] = False
        # Cast to OpenAI ResponseFormat TypedDict - SDK accepts this structure
        return cast(
            OpenAIResponseFormat,
            {
                "type": "json_schema",
                "json_schema": {
                    "name": get_schema_name(response_format.schema),
                    "strict": response_format.strict,
                    "schema": schema,
                },
            },
        )

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
