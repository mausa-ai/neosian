"""Anthropic Claude LLM client implementation."""

from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic, BadRequestError

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


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude LLM client.

    Uses the Anthropic SDK. Includes automatic retry when tool call
    generation fails.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key. Required, no implicit env var reading.
        """
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
    ) -> CompletionResponse:
        """Send a completion request to Anthropic.

        Automatically retries if tool call generation fails.
        After max retries, raises ToolCallGenerationError.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Optional list of tools the model can call.
            temperature: Sampling temperature (0.0-1.0). None uses default.

        Returns:
            CompletionResponse with the model's response.

        Raises:
            ToolCallGenerationError: If tool call generation fails after retries.
        """
        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        current_temp = (
            temperature if temperature is not None else LLMDefaults.TEMPERATURE
        )

        for attempt in range(LLMDefaults.MAX_TOOL_CALL_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": anthropic_messages,
                    "max_tokens": 8192,
                    "temperature": current_temp,
                }

                if system_prompt:
                    kwargs["system"] = system_prompt

                if anthropic_tools:
                    kwargs["tools"] = anthropic_tools

                response = await self._client.messages.create(**kwargs)
                return self._parse_response(response)

            except BadRequestError as e:
                if self._is_tool_call_error(e) and tools is not None:
                    if attempt < LLMDefaults.MAX_TOOL_CALL_RETRIES:
                        current_temp = LLMDefaults.RETRY_TEMPERATURE
                        continue
                    raise ToolCallGenerationError(
                        retries=LLMDefaults.MAX_TOOL_CALL_RETRIES
                    ) from e
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
        error_message = str(error).lower()
        return "tool" in error_message or "function" in error_message

    def _parse_response(self, response: object) -> CompletionResponse:
        """Parse Anthropic response into CompletionResponse.

        Args:
            response: Raw response from Anthropic API.

        Returns:
            Parsed CompletionResponse.
        """
        # Build text content and tool calls from content blocks
        text_content = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:  # type: ignore[attr-defined]
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=ToolCallId(block.id),
                        name=ToolName(block.name),
                        arguments=(
                            dict(block.input) if isinstance(block.input, dict) else {}
                        ),
                    )
                )

        return CompletionResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=text_content if text_content else None,
                tool_calls=tool_calls,
            ),
            usage=Usage(
                input_tokens=response.usage.input_tokens,  # type: ignore[attr-defined]
                output_tokens=response.usage.output_tokens,  # type: ignore[attr-defined]
            ),
            model=ModelId(response.model),  # type: ignore[attr-defined]
        )

    async def stream(
        self,
        messages: list[Message],
        model: ModelId,
        tools: list[ToolDefinition] | None = None,  # noqa: ARG002
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion request from Anthropic.

        Note: Agent uses blocking complete() for tool detection, then streams
        only the final natural language response. Tool handling not needed here.

        Args:
            messages: Conversation history.
            model: Model identifier.
            tools: Not used (agent handles tools via complete()).
            temperature: Sampling temperature (0.0-1.0). None uses default.

        Yields:
            StreamChunk objects as they arrive.
        """
        system_prompt, anthropic_messages = self._convert_messages(messages)
        temp = temperature if temperature is not None else LLMDefaults.TEMPERATURE

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": 8192,
            "temperature": temp,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        # Note: tools not passed - agent uses complete() for tool detection

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield StreamChunk(
                            content=event.delta.text,
                            finish_reason=None,
                        )
                elif event.type == "message_stop":
                    yield StreamChunk(
                        content=None,
                        finish_reason="stop",
                    )

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert internal messages to Anthropic format.

        Anthropic requires system message to be passed separately.

        Args:
            messages: Internal Message objects.

        Returns:
            Tuple of (system_prompt, messages_list).
        """
        system_prompt: str | None = None
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_prompt = msg.content
            elif msg.role == Role.USER:
                anthropic_messages.append(
                    {"role": "user", "content": msg.content or ""}
                )
            elif msg.role == Role.ASSISTANT:
                if msg.tool_calls:
                    content: list[dict[str, Any]] = []
                    if msg.content:
                        content.append({"type": "text", "text": msg.content})
                    for tc in msg.tool_calls:
                        content.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                    anthropic_messages.append({"role": "assistant", "content": content})
                else:
                    anthropic_messages.append(
                        {"role": "assistant", "content": msg.content or ""}
                    )
            elif msg.role == Role.TOOL:
                # Tool results in Anthropic are user messages with tool_result content
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content or "",
                            }
                        ],
                    }
                )

        return system_prompt, anthropic_messages

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert internal tool definitions to Anthropic format.

        Args:
            tools: Internal ToolDefinition objects.

        Returns:
            Anthropic-formatted tool definitions.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._client.close()
