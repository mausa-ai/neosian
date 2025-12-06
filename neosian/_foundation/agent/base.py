"""Agent core implementation.

Stateless agent that orchestrates LLM calls and tool execution.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from neosian._foundation.llm.base import (
    BaseLLMClient,
    Message,
    Role,
    ToolCall,
    ToolDefinition,
    Usage,
)
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.types import ModelId, SystemPrompt, ToolName
from neosian._foundation.tools.base import (
    ToolFunction,
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)


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
        agent = Agent(
            client=GroqClient(api_key="..."),
            model=ModelId("llama-3.3-70b-versatile"),
            system_prompt=SystemPrompt("You are helpful."),
            tools=[search_tool, calculate_tool],
        )

        response = await agent.run(messages)
    """

    def __init__(
        self,
        client: BaseLLMClient,
        model: ModelId,
        system_prompt: SystemPrompt,
        tools: list[ToolFunction] | None = None,
        max_tool_iterations: int = 10,
    ) -> None:
        """Initialize the agent.

        Args:
            client: LLM client to use.
            model: Model identifier.
            system_prompt: System prompt for the agent.
            tools: List of tool functions decorated with @Tool.
            max_tool_iterations: Maximum tool call iterations to prevent infinite loops.
        """
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._max_tool_iterations = max_tool_iterations

        # Build tool registry from decorated functions
        self._tools: dict[ToolName, ToolFunction] = {}
        self._tool_definitions: list[ToolDefinition] = []

        if tools:
            for tool_func in tools:
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

    async def run(self, messages: list[Message]) -> AgentResponse:
        """Execute the agent with the given conversation history.

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
