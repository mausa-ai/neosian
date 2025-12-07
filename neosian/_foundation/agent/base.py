"""Agent core implementation.

Stateless agent that orchestrates LLM calls and tool execution.
"""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, overload

from neosian._foundation.agent.streaming import (
    content_event,
    done_event,
    tool_call_event,
    tool_result_event,
)
from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.llm.groq import GroqClient
from neosian._foundation.llm.openai import OpenAIClient
from neosian._foundation.shared.constants import ErrorMessages, Provider
from neosian._foundation.shared.exceptions import MissingAPIKeyError
from neosian._foundation.shared.types import (
    AgentConfig,
    ModelId,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import (
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)
from neosian._foundation.tools.builtin.todo import TodoState, get_todo_tool


def _create_client(provider_id: str | None) -> tuple[BaseLLMClient, ModelId]:
    """Create LLM client based on provider.

    Args:
        provider_id: Provider identifier ("groq", "openai"). Defaults to "groq".

    Returns:
        Tuple of (client, default_model_id).

    Raises:
        MissingAPIKeyError: If required API key is not set.
        ValueError: If provider is not supported.
    """
    provider = provider_id or Provider.Groq.ID

    if provider == Provider.OpenAI.ID:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise MissingAPIKeyError(ErrorMessages.OPENAI_API_KEY_MISSING)
        return OpenAIClient(api_key=api_key), ModelId(Provider.OpenAI.DEFAULT_MODEL)

    if provider == Provider.Groq.ID:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise MissingAPIKeyError(ErrorMessages.GROQ_API_KEY_MISSING)
        return GroqClient(api_key=api_key), ModelId(Provider.Groq.DEFAULT_MODEL)

    raise ValueError(f"Unsupported provider: {provider}")


@dataclass
class AgentResponse:
    """Response from agent execution."""

    message: Message
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult[Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))


class Agent:
    """Stateless agent that orchestrates LLM and tool execution.

    The agent receives conversation history from the app, executes the
    tool loop, and returns the final response. It does not store state.

    Usage:
        from neosian import Agent, AgentConfig, Tool, ToolResult

        configuration = AgentConfig(
            system_prompt="You are helpful.",
            tools=[search_tool, calculate_tool],
        )

        agent = Agent(config=configuration)
        response = await agent.run(messages, stream=False)
    """

    def __init__(
        self,
        config: AgentConfig,
        max_tool_iterations: int = 10,
        todo_state: TodoState | None = None,
    ) -> None:
        """Initialize the agent.

        Args:
            config: Agent configuration with system_prompt, tools, provider, model.
            max_tool_iterations: Maximum tool call iterations to prevent infinite loops.
            todo_state: External todo state. If None and enable_todo=True, creates new.
        """
        # Create client based on provider
        client, default_model = _create_client(config.provider)
        self._client = client
        self._model = config.model if config.model else default_model
        self._system_prompt = config.system_prompt
        self._max_tool_iterations = max_tool_iterations

        # Build tool registry from decorated functions
        self._tools: dict[ToolName, ToolFunction] = {}
        self._tool_definitions: list[ToolDefinition] = []

        # Add global todo tool if enabled
        self._todo_state: TodoState | None = None
        if config.enable_todo:
            self._todo_state = todo_state if todo_state is not None else TodoState([])
            todo_tool = get_todo_tool(self._todo_state)
            self._register_tool(todo_tool)

        # Register user-provided tools
        for tool_func in config.tools:
            self._register_tool(tool_func)

    def _register_tool(self, tool_func: ToolFunction) -> None:
        """Register a single tool function.

        Args:
            tool_func: Tool function decorated with @Tool.

        Raises:
            ValueError: If function is not decorated with @Tool.
        """
        metadata = get_tool_metadata(tool_func)
        if metadata is None:
            raise ValueError(
                ErrorMessages.FUNCTION_NOT_DECORATED.format(
                    func_name=tool_func.__name__
                )
            )
        definition = get_tool_definition(tool_func)
        if definition is None:
            raise ValueError(
                ErrorMessages.FUNCTION_NOT_DECORATED.format(
                    func_name=tool_func.__name__
                )
            )
        self._tools[metadata.name] = tool_func
        self._tool_definitions.append(definition)

    @overload
    async def run(
        self, messages: list[Message], *, stream: Literal[False]
    ) -> AgentResponse: ...

    @overload
    async def run(
        self, messages: list[Message], *, stream: Literal[True]
    ) -> AsyncIterator[str]: ...

    async def run(
        self, messages: list[Message], *, stream: bool
    ) -> AgentResponse | AsyncIterator[str]:
        """Execute the agent with the given conversation history.

        Args:
            messages: Conversation history (without system message).
            stream: If True, yields SSE strings. If False, returns AgentResponse.

        Returns:
            AgentResponse when stream=False, AsyncIterator[str] when stream=True.
        """
        if stream:
            return self._run_streaming(messages)
        return await self._run_blocking(messages)

    async def _run_blocking(self, messages: list[Message]) -> AgentResponse:
        """Execute agent without streaming (blocking mode).

        Args:
            messages: Conversation history (without system message).

        Returns:
            AgentResponse with the final message and execution details.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult[Any]] = []
        total_usage = Usage(input_tokens=0, output_tokens=0)

        for _ in range(self._max_tool_iterations):
            # Get completion from LLM
            response = await self._client.complete(
                messages=full_messages,
                model=self._model,
                tools=self._tool_definitions if self._tool_definitions else None,
            )

            # Accumulate usage
            total_usage = Usage(
                input_tokens=total_usage.input_tokens + response.usage.input_tokens,
                output_tokens=total_usage.output_tokens + response.usage.output_tokens,
            )

            # If no tool calls, we're done
            if not response.message.tool_calls:
                return AgentResponse(
                    message=response.message,
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    usage=total_usage,
                )

            # Add assistant message with tool calls to history
            full_messages.append(response.message)

            # Execute each tool call
            for tool_call in response.message.tool_calls:
                all_tool_calls.append(tool_call)

                result = await self._execute_tool(tool_call)
                all_tool_results.append(result)

                # Add tool result to messages
                tool_result_content = self._format_tool_result(result)
                full_messages.append(
                    Message(
                        role=Role.TOOL,
                        content=tool_result_content,
                        tool_call_id=tool_call.id,
                    )
                )

        # Max iterations reached - return last response
        final_response = await self._client.complete(
            messages=full_messages,
            model=self._model,
            tools=None,  # No tools on final call to force text response
        )

        total_usage = Usage(
            input_tokens=total_usage.input_tokens + final_response.usage.input_tokens,
            output_tokens=total_usage.output_tokens
            + final_response.usage.output_tokens,
        )

        return AgentResponse(
            message=final_response.message,
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            usage=total_usage,
        )

    async def _run_streaming(self, messages: list[Message]) -> AsyncIterator[str]:
        """Execute agent with streaming (SSE mode).

        Tool calls are never streamed - they execute fully, then we stream
        only the final text response.

        Args:
            messages: Conversation history (without system message).

        Yields:
            SSE-formatted strings for tool calls, tool results, content, and done.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        for _ in range(self._max_tool_iterations):
            # Get completion from LLM (not streaming for tool call detection)
            response = await self._client.complete(
                messages=full_messages,
                model=self._model,
                tools=self._tool_definitions if self._tool_definitions else None,
            )

            # If no tool calls, stream the final response
            if not response.message.tool_calls:
                # Stream final response content
                async for sse in self._stream_final_response(full_messages):
                    yield sse
                return

            # Add assistant message with tool calls to history
            full_messages.append(response.message)

            # Execute each tool call and yield SSE events
            for tool_call in response.message.tool_calls:
                # Yield tool call event
                yield tool_call_event(tool_call).to_sse()

                # Execute tool
                result = await self._execute_tool(tool_call)

                # Yield tool result event
                yield tool_result_event(tool_call.id, result).to_sse()

                # Add tool result to messages
                tool_result_content = self._format_tool_result(result)
                full_messages.append(
                    Message(
                        role=Role.TOOL,
                        content=tool_result_content,
                        tool_call_id=tool_call.id,
                    )
                )

        # Max iterations reached - stream final response
        async for sse in self._stream_final_response(full_messages):
            yield sse

    async def _stream_final_response(
        self, full_messages: list[Message]
    ) -> AsyncIterator[str]:
        """Stream the final text response from the LLM.

        Args:
            full_messages: Full conversation history including system message.

        Yields:
            SSE-formatted strings for content chunks and done event.
        """
        stream = self._client.stream(
            messages=full_messages,
            model=self._model,
            tools=None,  # No tools on final call to force text response
        )

        async for chunk in stream:
            if chunk.content:
                yield content_event(chunk.content).to_sse()

            if chunk.finish_reason:
                yield done_event().to_sse()

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResult[Any]:
        """Execute a single tool call.

        Args:
            tool_call: The tool call to execute.

        Returns:
            ToolResult from the tool execution.
        """
        tool_func = self._tools.get(tool_call.name)

        if tool_func is None:
            return ToolResult.fail(
                ErrorMessages.TOOL_NOT_FOUND.format(tool_name=tool_call.name)
            )

        try:
            result = await tool_func(**tool_call.arguments)
            return result
        except TypeError as e:
            return ToolResult.fail(
                ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
                    tool_name=tool_call.name, error=e
                )
            )
        except Exception as e:
            return ToolResult.fail(
                ErrorMessages.TOOL_EXECUTION_FAILED.format(
                    tool_name=tool_call.name, error=e
                )
            )

    def _format_tool_result(self, result: ToolResult[Any]) -> str:
        """Format a tool result as a string for the LLM.

        Args:
            result: The tool result to format.

        Returns:
            JSON string representation of the result.
        """
        if result.success:
            return json.dumps({"success": True, "data": result.data})
        return json.dumps({"success": False, "error": result.error})
