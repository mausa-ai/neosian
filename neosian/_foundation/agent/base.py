"""Agent core implementation.

Stateless agent that orchestrates LLM calls and tool execution.
"""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, overload

from groq import AsyncGroq

from neosian._foundation.agent.streaming import (
    content_event,
    done_event,
    tool_call_event,
    tool_result_event,
)
from neosian._foundation.guardrails.checker import check_with_policy
from neosian._foundation.guardrails.classifier import check_with_classifier
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
from neosian._foundation.shared.constants import EnvVars, ErrorMessages, Provider
from neosian._foundation.shared.exceptions import (
    GuardrailStreamingError,
    MissingAPIKeyError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    ClassifierResult,
    GuardrailMode,
    GuardrailResult,
    ModelId,
    PolicyResult,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import (
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)
from neosian._foundation.tools.builtin.todo import TodoState, get_todo_tool


def _get_api_key(env_var: str) -> str:
    """Get API key from environment variable.

    Args:
        env_var: Environment variable name (EnvVars.GROQ_API_KEY or EnvVars.OPENAI_API_KEY).

    Returns:
        API key value.

    Raises:
        MissingAPIKeyError: If environment variable is not set.
    """
    api_key = os.environ.get(env_var)
    if not api_key:
        match env_var:
            case EnvVars.GROQ_API_KEY:
                raise MissingAPIKeyError(ErrorMessages.GROQ_API_KEY_MISSING)
            case EnvVars.OPENAI_API_KEY:
                raise MissingAPIKeyError(ErrorMessages.OPENAI_API_KEY_MISSING)
            case _:
                raise MissingAPIKeyError(f"{env_var} environment variable not set")
    return api_key


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
        api_key = _get_api_key(EnvVars.OPENAI_API_KEY)
        return OpenAIClient(api_key=api_key), ModelId(Provider.OpenAI.DEFAULT_MODEL)

    if provider == Provider.Groq.ID:
        api_key = _get_api_key(EnvVars.GROQ_API_KEY)
        return GroqClient(api_key=api_key), ModelId(Provider.Groq.DEFAULT_MODEL)

    raise ValueError(f"Unsupported provider: {provider}")


def _create_guardrail_client() -> AsyncGroq:
    """Create Groq client for guardrail models.

    Guardrails use Groq provider for both Llama Guard and GPT-OSS-Safeguard.

    Returns:
        AsyncGroq client for guardrail calls.

    Raises:
        MissingAPIKeyError: If GROQ_API_KEY is not set.
    """
    api_key = _get_api_key(EnvVars.GROQ_API_KEY)
    return AsyncGroq(api_key=api_key)


@dataclass
class AgentResponse:
    """Response from agent execution.

    Attributes:
        message: The assistant's response message.
        tool_calls_made: List of tool calls made during execution.
        tool_results: Results from tool executions.
        usage: Token usage statistics.
        blocked: True if content was blocked by guardrails.
        guardrail_result: Detailed guardrail check results (if guardrails enabled).
    """

    message: Message
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult[Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    blocked: bool = False
    guardrail_result: GuardrailResult | None = None


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

        # Store guardrails config and create client if needed
        self._guardrails = config.guardrails
        self._guardrail_client: AsyncGroq | None = None
        if self._guardrails is not None:
            self._guardrail_client = _create_guardrail_client()

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

        Raises:
            GuardrailStreamingError: If stream=True with output guardrails configured.
        """
        # Output guardrails require blocking mode
        if (
            stream
            and self._guardrails is not None
            and self._guardrails.has_output_guardrails
        ):
            raise GuardrailStreamingError()

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
        # Check input guardrails on user messages
        input_classifier_result: ClassifierResult | None = None
        input_policy_result: PolicyResult | None = None

        if self._guardrails is not None:
            # Extract user content for guardrail check
            user_content = self._extract_user_content(messages)
            if user_content:
                is_safe, input_classifier_result, input_policy_result = (
                    await self._check_guardrails(user_content, "input")
                )

                # Block if input flagged and block_on_input is True
                if not is_safe and self._guardrails.block_on_input:
                    return AgentResponse(
                        message=Message(role=Role.ASSISTANT, content=""),
                        blocked=True,
                        guardrail_result=GuardrailResult(
                            safe=False,
                            blocked_at="input",
                            input_classifier=input_classifier_result,
                            input_policy=input_policy_result,
                        ),
                    )

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

            # If no tool calls, we're done - check output guardrails
            if not response.message.tool_calls:
                return await self._finalize_response(
                    message=response.message,
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    usage=total_usage,
                    input_classifier=input_classifier_result,
                    input_policy=input_policy_result,
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

        return await self._finalize_response(
            message=final_response.message,
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            usage=total_usage,
            input_classifier=input_classifier_result,
            input_policy=input_policy_result,
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

    async def _check_guardrails(
        self,
        content: str,
        checkpoint: Literal["input", "output"],
    ) -> tuple[bool, ClassifierResult | None, PolicyResult | None]:
        """Check content against configured guardrails at the given checkpoint.

        Execution order depends on mode:
        - CLASSIFIER_ONLY: Run classifier only
        - POLICY_ONLY: Run policy only
        - CLASSIFIER_AND_POLICY: Run classifier, then always run policy
        - CLASSIFIER_THEN_POLICY: Run classifier, only run policy if classifier flags

        Args:
            content: Content to check.
            checkpoint: "input" or "output" checkpoint.

        Returns:
            Tuple of (is_safe, classifier_result, policy_result).
        """
        if self._guardrails is None or self._guardrail_client is None:
            return (True, None, None)

        # Select config fields based on checkpoint
        mode = (
            self._guardrails.input_mode
            if checkpoint == "input"
            else self._guardrails.output_mode
        )
        policy = (
            self._guardrails.input_policy
            if checkpoint == "input"
            else self._guardrails.output_policy
        )

        # No guardrails configured for this checkpoint
        if mode == GuardrailMode.NONE:
            return (True, None, None)

        classifier_result: ClassifierResult | None = None
        policy_result: PolicyResult | None = None

        # Run classifier if mode uses it
        if mode.uses_classifier():
            classifier_result = await check_with_classifier(
                content=content,
                client=self._guardrail_client,
            )

            # For CLASSIFIER_ONLY, return immediately based on classifier result
            if mode == GuardrailMode.CLASSIFIER_ONLY:
                return (classifier_result.safe, classifier_result, None)

            # For CLASSIFIER_THEN_POLICY, only run policy if classifier flags
            if mode == GuardrailMode.CLASSIFIER_THEN_POLICY and classifier_result.safe:
                # Classifier passed, skip policy (optimization)
                return (True, classifier_result, None)
            # Classifier flagged, continue to run policy for detailed analysis

        # Run policy if mode uses it
        if mode.uses_policy() and policy is not None:
            policy_result = await check_with_policy(
                content=content,
                policy=policy,
                client=self._guardrail_client,
            )

            # Determine overall safety
            is_safe = policy_result.safe
            if classifier_result is not None and not classifier_result.safe:
                is_safe = False

            return (is_safe, classifier_result, policy_result)

        # Fallback (shouldn't reach here with valid config)
        return (True, classifier_result, policy_result)

    def _extract_user_content(self, messages: list[Message]) -> str:
        """Extract user content from messages for guardrail checking.

        Returns the content of the last user message in the conversation.
        This assumes the app appends the new user input as the last message
        before calling agent.run().

        Expected app pattern:
            messages = db.load_history(user_id)  # Previous messages
            messages.append(Message(role=Role.USER, content=new_input))
            response = await agent.run(messages, stream=False)

        Args:
            messages: Conversation history.

        Returns:
            Content of the last user message, or empty string if none found.
        """
        for message in reversed(messages):
            if message.role == Role.USER and message.content:
                return message.content
        return ""

    async def _finalize_response(
        self,
        message: Message,
        tool_calls_made: list[ToolCall],
        tool_results: list[ToolResult[Any]],
        usage: Usage,
        input_classifier: ClassifierResult | None,
        input_policy: PolicyResult | None,
    ) -> AgentResponse:
        """Finalize response with output guardrails check.

        Checks output guardrails if configured, builds the GuardrailResult,
        and returns the final AgentResponse.

        Args:
            message: The assistant's response message.
            tool_calls_made: List of tool calls made during execution.
            tool_results: Results from tool executions.
            usage: Token usage statistics.
            input_classifier: Input classifier result (if run).
            input_policy: Input policy result (if run).

        Returns:
            AgentResponse with guardrail results populated.
        """
        output_classifier: ClassifierResult | None = None
        output_policy: PolicyResult | None = None
        is_output_safe = True

        # Check output guardrails if configured
        if (
            self._guardrails is not None
            and self._guardrails.has_output_guardrails
            and message.content
        ):
            is_output_safe, output_classifier, output_policy = (
                await self._check_guardrails(message.content, "output")
            )

        # Build guardrail result if any guardrails were run
        guardrail_result: GuardrailResult | None = None
        has_any_result = (
            input_classifier is not None
            or input_policy is not None
            or output_classifier is not None
            or output_policy is not None
        )

        if has_any_result:
            # Determine input safety (for metadata, not blocking - that's handled earlier)
            is_input_safe = True
            if input_classifier is not None and not input_classifier.safe:
                is_input_safe = False
            if input_policy is not None and not input_policy.safe:
                is_input_safe = False

            overall_safe = is_input_safe and is_output_safe

            # Determine where flagged (if any)
            blocked_at: Literal["input", "output"] | None = None
            if not is_input_safe:
                blocked_at = "input"
            elif not is_output_safe:
                blocked_at = "output"

            guardrail_result = GuardrailResult(
                safe=overall_safe,
                blocked_at=blocked_at,
                input_classifier=input_classifier,
                input_policy=input_policy,
                output_classifier=output_classifier,
                output_policy=output_policy,
            )

        return AgentResponse(
            message=message,
            tool_calls_made=tool_calls_made,
            tool_results=tool_results,
            usage=usage,
            blocked=not is_output_safe,
            guardrail_result=guardrail_result,
        )
