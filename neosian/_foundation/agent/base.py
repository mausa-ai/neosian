"""Agent core implementation.

Stateless agent that orchestrates LLM calls and tool execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, overload

from groq import AsyncGroq
from pydantic import BaseModel

if TYPE_CHECKING:
    from neosian._foundation.agent.session import AgentSession

from neosian._foundation.agent.streaming import (
    SSEEventEmitter,
    blocked_event,
    content_event,
    done_event,
    heartbeat_event,
    reasoning_event,
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
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.shared.constants import EnvVars, ErrorMessages, Streaming
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    GuardrailStreamingError,
    MissingAPIKeyError,
    ModelFailedError,
    StructuredOutputStreamingError,
    StructuredOutputToolsError,
)
from neosian._foundation.shared.types import (
    AgentConfig,
    ClassifierResult,
    FallbackState,
    GuardrailErrorPolicy,
    GuardrailMode,
    GuardrailResult,
    Model,
    PolicyResult,
    ReasoningEffort,
    ResponseFormat,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import (
    ToolResult,
    get_tool_definition,
    get_tool_metadata,
)
from neosian._foundation.tools.builtin.todo import update_todo

logger = logging.getLogger(__name__)


def _get_api_key(env_var: str) -> str:
    """Get API key from environment variable.

    Args:
        env_var: Environment variable name.

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
            case EnvVars.ANTHROPIC_API_KEY:
                raise MissingAPIKeyError(ErrorMessages.ANTHROPIC_API_KEY_MISSING)
            case _:
                raise MissingAPIKeyError(f"{env_var} environment variable not set")
    return api_key


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
        parsed: Parsed Pydantic model instance when response_format was provided.
            None when response_format was not used.
    """

    message: Message
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult[Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    blocked: bool = False
    guardrail_result: GuardrailResult | None = None
    parsed: BaseModel | None = None


class Agent:
    """Stateless agent that orchestrates LLM and tool execution.

    The agent receives conversation history from the app, executes the
    tool loop, and returns the final response. It does not store state.

    Fallback behavior:
    - If no fallback is configured and the model fails, raises ModelFailedError.
    - If fallback is configured and both models fail, raises FallbackExhaustedError.
    - With sessions, fallback is "sticky" - subsequent calls continue using the
      fallback model until retry_main_after successful calls.

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
    ) -> None:
        """Initialize the agent.

        Args:
            config: Agent configuration with system_prompt, tools, model.
            max_tool_iterations: Maximum tool call iterations to prevent infinite loops.
        """
        # Initialize router for provider management
        self._router = ProviderRouter()

        # Model and fallback configuration
        self._model = config.model
        self._fallback = config.fallback
        self._system_prompt = config.system_prompt
        self._max_tool_iterations = max_tool_iterations
        self._reasoning_effort = config.reasoning_effort

        # Store guardrails config and create client if needed
        self._guardrails = config.guardrails
        self._guardrail_client: AsyncGroq | None = None
        if self._guardrails is not None:
            self._guardrail_client = _create_guardrail_client()

        # Build tool registry from decorated functions
        self._tools: dict[ToolName, ToolFunction] = {}
        self._tool_definitions: list[ToolDefinition] = []

        # Add global todo tool if enabled
        if config.enable_todo:
            self._register_tool(update_todo)

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
        self,
        messages: list[Message],
        *,
        stream: Literal[False],
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse: ...

    @overload
    async def run(
        self,
        messages: list[Message],
        *,
        stream: Literal[True],
        response_format: None = None,
    ) -> AsyncIterator[str]: ...

    async def run(
        self,
        messages: list[Message],
        *,
        stream: bool,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse | AsyncIterator[str]:
        """Execute the agent with the given conversation history.

        Args:
            messages: Conversation history (without system message).
            stream: If True, yields SSE strings. If False, returns AgentResponse.
            response_format: Optional structured output configuration. When provided,
                the agent returns a structured response matching the Pydantic schema.
                Incompatible with stream=True and tool-enabled agents.

        Returns:
            AgentResponse when stream=False, AsyncIterator[str] when stream=True.

        Raises:
            GuardrailStreamingError: If stream=True with output guardrails configured.
            StructuredOutputStreamingError: If stream=True with response_format.
            StructuredOutputToolsError: If response_format with tool-enabled agent.
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        # Structured outputs require blocking mode
        if response_format is not None and stream:
            raise StructuredOutputStreamingError()

        # Structured outputs are incompatible with tool-enabled agents
        if response_format is not None and self._tool_definitions:
            raise StructuredOutputToolsError()

        # Output guardrails require blocking mode
        if (
            stream
            and self._guardrails is not None
            and self._guardrails.has_output_guardrails
        ):
            raise GuardrailStreamingError()

        if stream:
            return self._run_streaming(messages)
        return await self._run_blocking(messages, response_format=response_format)

    async def _run_blocking(
        self,
        messages: list[Message],
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute agent without streaming.

        Input guardrails run in parallel with agent execution for optimal latency.
        Safe users experience no guardrail overhead. If guard flags and block_on_input
        is True, agent response is discarded.

        Args:
            messages: Conversation history (without system message).
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.
        """
        # No input guardrails configured - run agent directly
        if (
            self._guardrails is None
            or self._guardrails.input_mode == GuardrailMode.NONE
        ):
            return await self._execute_agent_core(
                messages, response_format=response_format
            )

        # Extract user content for guardrail check
        user_content = self._extract_user_content(messages)
        if not user_content:
            return await self._execute_agent_core(
                messages, response_format=response_format
            )

        # Run guard and agent in parallel
        guard_task = asyncio.create_task(self._check_guardrails(user_content, "input"))
        agent_task = asyncio.create_task(
            self._execute_agent_core(messages, response_format=response_format)
        )

        # Wait for first to complete
        done, pending = await asyncio.wait(
            [guard_task, agent_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Case 1: Guard finished first
        if guard_task in done and agent_task in pending:
            is_safe, input_classifier, input_policy = self._get_guard_result_safe(
                guard_task
            )

            if not is_safe and self._guardrails.block_on_input:
                # Cancel agent to save resources
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                return AgentResponse(
                    message=Message(role=Role.ASSISTANT, content=""),
                    blocked=True,
                    guardrail_result=GuardrailResult(
                        safe=False,
                        flagged_at="input",
                        input_classifier=input_classifier,
                        input_policy=input_policy,
                    ),
                )

            # Safe or block_on_input=False - wait for agent and attach guard results
            agent_response = await agent_task
            return self._attach_input_guard_results(
                agent_response, input_classifier, input_policy
            )

        # Case 2: Agent finished first
        agent_response = agent_task.result()

        # Still need guard verdict (with error handling)
        is_safe, input_classifier, input_policy = await self._await_guard_result_safe(
            guard_task
        )

        if not is_safe and self._guardrails.block_on_input:
            # Agent ran but we must block - discard response
            return AgentResponse(
                message=Message(role=Role.ASSISTANT, content=""),
                blocked=True,
                guardrail_result=GuardrailResult(
                    safe=False,
                    flagged_at="input",
                    input_classifier=input_classifier,
                    input_policy=input_policy,
                ),
            )

        # Safe or block_on_input=False - return with guard results
        return self._attach_input_guard_results(
            agent_response, input_classifier, input_policy
        )

    async def _execute_agent_core(
        self,
        messages: list[Message],
        fallback_state: FallbackState | None = None,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute the agent LLM and tool loop with optional fallback.

        This is the core agent execution without input guardrail checks.
        Used by both blocking and streaming modes.

        Args:
            messages: Conversation history (without system message).
            fallback_state: Optional fallback state for session-based sticky fallback.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.

        Raises:
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        # Determine which model to try first - check if we should retry main
        if (
            fallback_state is not None
            and fallback_state.using_fallback
            and self._should_retry_main(fallback_state)
        ):
            logger.info(
                ErrorMessages.FALLBACK_RETRY_MAIN.format(
                    main_model=self._model.value,
                    successful_calls=fallback_state.successful_fallback_calls,
                )
            )
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0

        # If using fallback (sticky), try fallback first
        if fallback_state is not None and fallback_state.using_fallback:
            return await self._execute_with_fallback_model(
                full_messages, fallback_state, response_format
            )

        # Try main model
        try:
            client = self._router.create_client(self._model.provider)
            response = await self._execute_with_client(
                client=client,
                model=self._model,
                full_messages=full_messages,
                response_format=response_format,
            )
            # Success on main - reset fallback state if present
            if fallback_state is not None:
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
            return response
        except Exception as e:
            main_error = str(e)
            # No fallback configured - raise immediately
            if self._fallback is None:
                raise ModelFailedError(
                    model=self._model.value,
                    error=main_error,
                    has_fallback=False,
                ) from e

            # Try fallback
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._model.value,
                    to_model=self._fallback.model.value,
                    reason=main_error,
                )
            )
            try:
                fallback_client = self._router.create_client(
                    self._fallback.model.provider
                )
                response = await self._execute_with_client(
                    client=fallback_client,
                    model=self._fallback.model,
                    full_messages=full_messages,
                    response_format=response_format,
                )
                # Success on fallback - update state
                if fallback_state is not None:
                    fallback_state.using_fallback = True
                    fallback_state.successful_fallback_calls = 1
                return response
            except Exception as fallback_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=main_error,
                    fallback_model=self._fallback.model.value,
                    fallback_error=str(fallback_e),
                ) from fallback_e

    async def _execute_with_fallback_model(
        self,
        full_messages: list[Message],
        fallback_state: FallbackState,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute with fallback model (sticky mode).

        Args:
            full_messages: Full conversation with system message prepended.
            fallback_state: Fallback state tracking.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.

        Raises:
            FallbackExhaustedError: If fallback model fails.
        """
        if self._fallback is None:
            raise ModelFailedError(
                model=self._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )

        try:
            client = self._router.create_client(self._fallback.model.provider)
            response = await self._execute_with_client(
                client=client,
                model=self._fallback.model,
                full_messages=full_messages,
                response_format=response_format,
            )
            # Success - increment counter
            fallback_state.successful_fallback_calls += 1
            return response
        except Exception as e:
            # Fallback failed - try main as last resort
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._fallback.model.value,
                    to_model=self._model.value,
                    reason=str(e),
                )
            )
            fallback_error = str(e)
            try:
                main_client = self._router.create_client(self._model.provider)
                response = await self._execute_with_client(
                    client=main_client,
                    model=self._model,
                    full_messages=full_messages,
                    response_format=response_format,
                )
                # Main recovered - reset state
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
                return response
            except Exception as main_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=str(main_e),
                    fallback_model=self._fallback.model.value,
                    fallback_error=fallback_error,
                ) from main_e

    def _should_retry_main(self, fallback_state: FallbackState) -> bool:
        """Check if we should retry the main model.

        Args:
            fallback_state: Current fallback state.

        Returns:
            True if we should retry main model, False otherwise.
        """
        if self._fallback is None:
            return False
        if self._fallback.retry_main_after == 0:
            return False  # Never retry
        return (
            fallback_state.successful_fallback_calls >= self._fallback.retry_main_after
        )

    async def _execute_with_client(
        self,
        client: BaseLLMClient,
        model: Model,
        full_messages: list[Message],
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute the agent with a specific client and model.

        Args:
            client: LLM client to use.
            model: Model identifier.
            full_messages: Full conversation with system message prepended.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.
        """
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult[Any]] = []
        total_usage = Usage(input_tokens=0, output_tokens=0)

        # Silently drop reasoning_effort if model doesn't support it (graceful fallback)
        effective_reasoning = (
            self._reasoning_effort if model.supports_reasoning else None
        )

        for _ in range(self._max_tool_iterations):
            # Get completion from LLM
            response = await client.complete(
                messages=full_messages,
                model=model,
                tools=self._tool_definitions if self._tool_definitions else None,
                response_format=response_format,
                reasoning_effort=effective_reasoning,
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
                    response_format=response_format,
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
        final_response = await client.complete(
            messages=full_messages,
            model=model,
            tools=None,  # No tools on final call to force text response
            response_format=response_format,
            reasoning_effort=effective_reasoning,
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
            response_format=response_format,
        )

    def _attach_input_guard_results(
        self,
        response: AgentResponse,
        input_classifier: ClassifierResult | None,
        input_policy: PolicyResult | None,
    ) -> AgentResponse:
        """Attach input guardrail results to an existing AgentResponse.

        Used when agent finishes before guard in parallel execution.

        Args:
            response: The agent response to augment.
            input_classifier: Input classifier result.
            input_policy: Input policy result.

        Returns:
            AgentResponse with updated guardrail_result.
        """
        if input_classifier is None and input_policy is None:
            return response

        # Determine input safety
        is_input_safe = True
        if input_classifier is not None and not input_classifier.safe:
            is_input_safe = False
        if input_policy is not None and not input_policy.safe:
            is_input_safe = False

        # Merge with existing guardrail result (from output check)
        existing = response.guardrail_result
        if existing is not None:
            overall_safe = is_input_safe and existing.safe
            flagged_at = "input" if not is_input_safe else existing.flagged_at
            guardrail_result = GuardrailResult(
                safe=overall_safe,
                flagged_at=flagged_at,
                input_classifier=input_classifier,
                input_policy=input_policy,
                output_classifier=existing.output_classifier,
                output_policy=existing.output_policy,
            )
        else:
            guardrail_result = GuardrailResult(
                safe=is_input_safe,
                flagged_at="input" if not is_input_safe else None,
                input_classifier=input_classifier,
                input_policy=input_policy,
            )

        return AgentResponse(
            message=response.message,
            tool_calls_made=response.tool_calls_made,
            tool_results=response.tool_results,
            usage=response.usage,
            blocked=response.blocked,
            guardrail_result=guardrail_result,
        )

    async def _run_streaming(self, messages: list[Message]) -> AsyncIterator[str]:
        """Execute agent with streaming (SSE mode).

        Input guardrails run in parallel with streaming. If guard flags and
        block_on_input is True, emits BLOCKED event to interrupt the stream.
        Safe users experience no guardrail overhead.

        Args:
            messages: Conversation history (without system message).

        Yields:
            SSE-formatted strings for tool calls, tool results, content, blocked, and done.
        """
        # Determine if we need to run input guardrails
        has_input_guard = (
            self._guardrails is not None
            and self._guardrails.input_mode != GuardrailMode.NONE
        )
        user_content = self._extract_user_content(messages) if has_input_guard else ""

        # Start guard task in background if needed
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ) = None
        if has_input_guard and user_content:
            guard_task = asyncio.create_task(
                self._check_guardrails(user_content, "input")
            )

        # Stream the agent response, checking guard status periodically
        async for sse in self._stream_agent_with_guard(messages, guard_task):
            yield sse

    async def _stream_agent_with_guard(
        self,
        messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        fallback_state: FallbackState | None = None,
    ) -> AsyncIterator[str]:
        """Stream agent response while monitoring guard task.

        Args:
            messages: Conversation history (without system message).
            guard_task: Background guard task to monitor (or None if no guard).
            fallback_state: Optional fallback state for session-based sticky fallback.

        Yields:
            SSE-formatted strings with sequence and created_at metadata.

        Raises:
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        # Create emitter once for the entire streaming session
        emitter = SSEEventEmitter()

        # Determine which model to try first - check if we should retry main
        if (
            fallback_state is not None
            and fallback_state.using_fallback
            and self._should_retry_main(fallback_state)
        ):
            logger.info(
                ErrorMessages.FALLBACK_RETRY_MAIN.format(
                    main_model=self._model.value,
                    successful_calls=fallback_state.successful_fallback_calls,
                )
            )
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0

        # If using fallback (sticky), try fallback first
        if fallback_state is not None and fallback_state.using_fallback:
            async for sse in self._stream_with_fallback_model(
                full_messages, guard_task, emitter, fallback_state
            ):
                yield sse
            return

        # Try main model
        try:
            client = self._router.create_client(self._model.provider)
            async for sse in self._stream_with_client(
                client=client,
                model=self._model,
                full_messages=full_messages,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Success on main - reset fallback state if present
            if fallback_state is not None:
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
            return
        except Exception as e:
            main_error = str(e)
            # No fallback configured - raise immediately
            if self._fallback is None:
                raise ModelFailedError(
                    model=self._model.value,
                    error=main_error,
                    has_fallback=False,
                ) from e

            # Try fallback
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._model.value,
                    to_model=self._fallback.model.value,
                    reason=main_error,
                )
            )
            try:
                fallback_client = self._router.create_client(
                    self._fallback.model.provider
                )
                async for sse in self._stream_with_client(
                    client=fallback_client,
                    model=self._fallback.model,
                    full_messages=full_messages,
                    guard_task=guard_task,
                    emitter=emitter,
                ):
                    yield sse
                # Success on fallback - update state
                if fallback_state is not None:
                    fallback_state.using_fallback = True
                    fallback_state.successful_fallback_calls = 1
                return
            except Exception as fallback_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=main_error,
                    fallback_model=self._fallback.model.value,
                    fallback_error=str(fallback_e),
                ) from fallback_e

    async def _stream_with_fallback_model(
        self,
        full_messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        emitter: SSEEventEmitter,
        fallback_state: FallbackState,
    ) -> AsyncIterator[str]:
        """Stream with fallback model (sticky mode).

        Args:
            full_messages: Full conversation with system message prepended.
            guard_task: Background guard task to monitor (or None).
            emitter: SSE event emitter for metadata.
            fallback_state: Fallback state tracking.

        Yields:
            SSE-formatted strings.

        Raises:
            FallbackExhaustedError: If both models fail.
        """
        if self._fallback is None:
            raise ModelFailedError(
                model=self._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )

        try:
            client = self._router.create_client(self._fallback.model.provider)
            async for sse in self._stream_with_client(
                client=client,
                model=self._fallback.model,
                full_messages=full_messages,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Success - increment counter
            fallback_state.successful_fallback_calls += 1
            return
        except Exception as e:
            # Fallback failed - try main as last resort
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._fallback.model.value,
                    to_model=self._model.value,
                    reason=str(e),
                )
            )
            fallback_error = str(e)
            try:
                main_client = self._router.create_client(self._model.provider)
                async for sse in self._stream_with_client(
                    client=main_client,
                    model=self._model,
                    full_messages=full_messages,
                    guard_task=guard_task,
                    emitter=emitter,
                ):
                    yield sse
                # Main recovered - reset state
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
                return
            except Exception as main_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=str(main_e),
                    fallback_model=self._fallback.model.value,
                    fallback_error=fallback_error,
                ) from main_e

    async def _stream_with_client(
        self,
        client: BaseLLMClient,
        model: Model,
        full_messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        emitter: SSEEventEmitter | None = None,
    ) -> AsyncIterator[str]:
        """Stream agent response with a specific client while monitoring guard task.

        Args:
            client: LLM client to use.
            model: Model identifier.
            full_messages: Full conversation with system message prepended.
            guard_task: Background guard task to monitor (or None if no guard).
            emitter: SSE event emitter for metadata. If None, creates a new one.

        Yields:
            SSE-formatted strings with sequence and created_at metadata.
        """
        if emitter is None:
            emitter = SSEEventEmitter()

        # Silently drop reasoning_effort if model doesn't support it (graceful fallback)
        effective_reasoning = (
            self._reasoning_effort if model.supports_reasoning else None
        )

        for _ in range(self._max_tool_iterations):
            # Check guard before each LLM call
            if guard_task is not None and guard_task.done():
                blocked_event_sse = self._check_guard_and_block(guard_task, emitter)
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None  # Don't check again

            # Get completion from LLM (not streaming for tool call detection)
            response = await client.complete(
                messages=full_messages,
                model=model,
                tools=self._tool_definitions if self._tool_definitions else None,
                reasoning_effort=effective_reasoning,
            )

            # If no tool calls, stream the final response
            if not response.message.tool_calls:
                async for sse in self._stream_final_with_client_and_guard(
                    client=client,
                    model=model,
                    full_messages=full_messages,
                    guard_task=guard_task,
                    emitter=emitter,
                    reasoning_effort=effective_reasoning,
                ):
                    yield sse
                return

            # Add assistant message with tool calls to history
            full_messages.append(response.message)

            # Execute each tool call and yield SSE events
            for tool_call in response.message.tool_calls:
                # Check guard before each tool call
                if guard_task is not None and guard_task.done():
                    blocked_event_sse = self._check_guard_and_block(guard_task, emitter)
                    if blocked_event_sse:
                        yield blocked_event_sse
                        return
                    guard_task = None

                # Yield tool call event
                yield emitter.emit(tool_call_event(tool_call))

                # Execute tool with heartbeats for long-running tools
                tool_result: ToolResult[Any] | None = None
                async for result, heartbeat_sse in self._execute_tool_with_heartbeats(
                    tool_call, emitter
                ):
                    if heartbeat_sse is not None:
                        yield heartbeat_sse
                    if result is not None:
                        tool_result = result

                # Yield tool result event
                assert tool_result is not None  # Always set after loop completes
                yield emitter.emit(tool_result_event(tool_call.id, tool_result))

                # Add tool result to messages
                tool_result_content = self._format_tool_result(tool_result)
                full_messages.append(
                    Message(
                        role=Role.TOOL,
                        content=tool_result_content,
                        tool_call_id=tool_call.id,
                    )
                )

        # Max iterations reached - stream final response
        async for sse in self._stream_final_with_client_and_guard(
            client=client,
            model=model,
            full_messages=full_messages,
            guard_task=guard_task,
            emitter=emitter,
            reasoning_effort=effective_reasoning,
        ):
            yield sse

    def _get_guard_result_safe(
        self,
        guard_task: asyncio.Task[
            tuple[bool, ClassifierResult | None, PolicyResult | None]
        ],
    ) -> tuple[bool, ClassifierResult | None, PolicyResult | None]:
        """Get guard task result with error policy handling.

        Handles exceptions from guard task based on configured error_policy:
        - FAIL_OPEN: On error, treat as safe (log warning)
        - FAIL_CLOSED: On error, treat as blocked (log error)

        Args:
            guard_task: Completed guard task.

        Returns:
            Tuple of (is_safe, classifier_result, policy_result).
        """
        try:
            return guard_task.result()
        except Exception as e:
            return self._handle_guard_error(e)

    async def _await_guard_result_safe(
        self,
        guard_task: asyncio.Task[
            tuple[bool, ClassifierResult | None, PolicyResult | None]
        ],
    ) -> tuple[bool, ClassifierResult | None, PolicyResult | None]:
        """Await guard task result with error policy handling.

        Async version of _get_guard_result_safe for awaiting pending tasks.

        Args:
            guard_task: Guard task to await.

        Returns:
            Tuple of (is_safe, classifier_result, policy_result).
        """
        try:
            return await guard_task
        except Exception as e:
            return self._handle_guard_error(e)

    def _handle_guard_error(
        self, error: Exception
    ) -> tuple[bool, ClassifierResult | None, PolicyResult | None]:
        """Handle guardrail error based on error_policy.

        Args:
            error: The exception that occurred.

        Returns:
            Tuple of (is_safe, None, None) based on error policy.
        """
        error_policy = (
            self._guardrails.error_policy
            if self._guardrails
            else GuardrailErrorPolicy.FAIL_OPEN
        )

        if error_policy == GuardrailErrorPolicy.FAIL_OPEN:
            logger.warning(
                "Guardrail check failed (fail-open): %s. Treating as safe.",
                str(error),
            )
            return (True, None, None)
        else:
            logger.error(
                "Guardrail check failed (fail-closed): %s. Treating as blocked.",
                str(error),
            )
            return (False, None, None)

    def _check_guard_and_block(
        self,
        guard_task: asyncio.Task[
            tuple[bool, ClassifierResult | None, PolicyResult | None]
        ],
        emitter: SSEEventEmitter | None = None,
    ) -> str | None:
        """Check completed guard task and return blocked event if needed.

        Handles guard task errors based on error_policy configuration.

        Args:
            guard_task: Completed guard task.
            emitter: Optional SSE event emitter for metadata.

        Returns:
            Blocked SSE string if guard flagged and block_on_input=True, else None.
        """
        is_safe, classifier, policy = self._get_guard_result_safe(guard_task)

        if not is_safe and self._guardrails and self._guardrails.block_on_input:
            categories = classifier.categories if classifier else None
            rationale = policy.rationale if policy else None
            event = blocked_event(categories=categories, rationale=rationale)
            if emitter is not None:
                return emitter.emit(event)
            return event.to_sse()

        return None

    async def _stream_final_with_client_and_guard(
        self,
        client: BaseLLMClient,
        model: Model,
        full_messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        emitter: SSEEventEmitter,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> AsyncIterator[str]:
        """Stream final response with a specific client while monitoring guard task.

        Args:
            client: LLM client to use.
            model: Model identifier.
            full_messages: Full conversation history including system message.
            guard_task: Background guard task to monitor (or None).
            emitter: SSE event emitter for metadata (required, passed from caller).
            reasoning_effort: Optional reasoning effort (pre-filtered for model support).

        Yields:
            SSE-formatted strings for content chunks, blocked, and done event.
        """
        stream = client.stream(
            messages=full_messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
        )

        # Track usage and pending done for different provider patterns
        # OpenAI/Groq: usage comes in separate chunk after finish_reason
        # Anthropic: usage comes with finish_reason chunk
        pending_done = False
        final_usage: Usage | None = None

        async for chunk in stream:
            # Check guard during streaming
            if guard_task is not None and guard_task.done():
                blocked_event_sse = self._check_guard_and_block(guard_task, emitter)
                if blocked_event_sse:
                    yield blocked_event_sse
                    return
                guard_task = None

            # Emit reasoning before content (for reasoning models)
            if chunk.reasoning:
                yield emitter.emit(reasoning_event(chunk.reasoning))

            if chunk.content:
                yield emitter.emit(content_event(chunk.content))

            if chunk.finish_reason:
                # Final guard check before done (with error handling)
                if guard_task is not None:
                    is_safe, classifier, policy = await self._await_guard_result_safe(
                        guard_task
                    )
                    if (
                        not is_safe
                        and self._guardrails
                        and self._guardrails.block_on_input
                    ):
                        categories = classifier.categories if classifier else None
                        rationale = policy.rationale if policy else None
                        yield emitter.emit(
                            blocked_event(categories=categories, rationale=rationale)
                        )
                        return

                if chunk.usage:
                    # Anthropic: usage comes with finish_reason
                    yield emitter.emit(done_event(chunk.usage))
                else:
                    # OpenAI/Groq: usage may come in next chunk
                    pending_done = True

            # Handle usage-only chunk (OpenAI/Groq pattern)
            if chunk.usage and not chunk.finish_reason and not chunk.content:
                final_usage = chunk.usage
                if pending_done:
                    yield emitter.emit(done_event(final_usage))
                    pending_done = False

        # If we have a pending done without usage, emit it now
        if pending_done:
            yield emitter.emit(done_event(final_usage))

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

    async def _execute_tool_with_heartbeats(
        self,
        tool_call: ToolCall,
        emitter: SSEEventEmitter,
    ) -> AsyncIterator[tuple[ToolResult[Any] | None, str | None]]:
        """Execute a tool while emitting heartbeat events.

        Runs the tool in a background task and emits heartbeat SSE events
        at regular intervals to keep the connection alive during long-running
        tool executions.

        Args:
            tool_call: The tool call to execute.
            emitter: SSE event emitter for heartbeat events.

        Yields:
            Tuples of (result, heartbeat_sse):
            - (None, heartbeat_sse) for heartbeat events during execution
            - (result, None) when tool execution completes
        """
        start_time = time.monotonic()
        tool_task = asyncio.create_task(self._execute_tool(tool_call))
        interval = Streaming.HEARTBEAT_INTERVAL_SECONDS

        while not tool_task.done():
            try:
                # Wait for tool to complete or timeout
                await asyncio.wait_for(
                    asyncio.shield(tool_task),
                    timeout=interval,
                )
            except TimeoutError:
                # Tool still running - emit heartbeat
                elapsed = time.monotonic() - start_time
                yield (None, emitter.emit(heartbeat_event(tool_call.id, elapsed)))

        # Tool completed - yield result
        yield (tool_task.result(), None)

    def _format_tool_result(self, result: ToolResult[Any]) -> str:
        """Format a tool result as a string for the LLM.

        Args:
            result: The tool result to format.

        Returns:
            JSON string representation of the result.
        """
        return result.to_json()

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
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Finalize response with output guardrails check and structured output parsing.

        Checks output guardrails if configured and returns the final AgentResponse.
        Input guardrail results are attached separately via _attach_input_guard_results.

        Args:
            message: The assistant's response message.
            tool_calls_made: List of tool calls made during execution.
            tool_results: Results from tool executions.
            usage: Token usage statistics.
            response_format: Optional structured output configuration for parsing.

        Returns:
            AgentResponse with output guardrail results and parsed content (if any).
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

        # Build guardrail result if output guardrails were run
        guardrail_result: GuardrailResult | None = None
        if output_classifier is not None or output_policy is not None:
            guardrail_result = GuardrailResult(
                safe=is_output_safe,
                flagged_at="output" if not is_output_safe else None,
                output_classifier=output_classifier,
                output_policy=output_policy,
            )

        # Parse structured output if response_format was provided
        parsed: BaseModel | None = None
        if response_format is not None and message.content:
            from neosian._foundation.shared.schema import validate_json

            parsed = validate_json(response_format.schema, message.content)

        return AgentResponse(
            message=message,
            tool_calls_made=tool_calls_made,
            tool_results=tool_results,
            usage=usage,
            blocked=not is_output_safe,
            guardrail_result=guardrail_result,
            parsed=parsed,
        )

    # =========================================================================
    # Session support - methods for AgentSession to call
    # =========================================================================

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AgentSession]:
        """Create a session with persistent LLM clients.

        Sessions cache LLM clients across multiple run() calls, eliminating
        the connection establishment overhead that occurs when creating new
        clients for each request.

        Thread-safety: Sessions are safe for concurrent use. The underlying
        SDK clients use httpx which supports concurrent requests.

        Usage patterns:
            - Web servers: Create one session at startup via lifespan
            - Batch processing: Wrap the entire batch in a single session
            - CLI chat loops: Wrap the conversation loop in a session
            - One-off calls: Use agent.run() directly (no session needed)

        Example:
            async with agent.session() as session:
                response1 = await session.run(messages1, stream=False)
                response2 = await session.run(messages2, stream=False)
            # Clients automatically closed here

        Yields:
            AgentSession instance for executing runs with cached clients.
        """
        from neosian._foundation.agent.session import AgentSession

        session_instance = AgentSession(self)
        try:
            yield session_instance
        finally:
            await session_instance.close()

    async def _run_blocking_with_session(
        self,
        messages: list[Message],
        session: AgentSession,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute agent without streaming, using session's cached clients.

        This is the session-aware version of _run_blocking. It delegates
        to _execute_agent_core_with_session for the actual execution.

        Args:
            messages: Conversation history (without system message).
            session: The session providing cached clients.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.
        """
        # No input guardrails configured - run agent directly
        if (
            self._guardrails is None
            or self._guardrails.input_mode == GuardrailMode.NONE
        ):
            return await self._execute_agent_core_with_session(
                messages, session, response_format=response_format
            )

        # Extract user content for guardrail check
        user_content = self._extract_user_content(messages)
        if not user_content:
            return await self._execute_agent_core_with_session(
                messages, session, response_format=response_format
            )

        # Run guard and agent in parallel
        guard_task = asyncio.create_task(self._check_guardrails(user_content, "input"))
        agent_task = asyncio.create_task(
            self._execute_agent_core_with_session(
                messages, session, response_format=response_format
            )
        )

        # Wait for first to complete
        done, pending = await asyncio.wait(
            [guard_task, agent_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Case 1: Guard finished first
        if guard_task in done and agent_task in pending:
            is_safe, input_classifier, input_policy = self._get_guard_result_safe(
                guard_task
            )

            if not is_safe and self._guardrails.block_on_input:
                # Cancel agent to save resources
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                return AgentResponse(
                    message=Message(role=Role.ASSISTANT, content=""),
                    blocked=True,
                    guardrail_result=GuardrailResult(
                        safe=False,
                        flagged_at="input",
                        input_classifier=input_classifier,
                        input_policy=input_policy,
                    ),
                )

            # Safe or block_on_input=False - wait for agent and attach guard results
            agent_response = await agent_task
            return self._attach_input_guard_results(
                agent_response, input_classifier, input_policy
            )

        # Case 2: Agent finished first
        agent_response = agent_task.result()

        # Still need guard verdict (with error handling)
        is_safe, input_classifier, input_policy = await self._await_guard_result_safe(
            guard_task
        )

        if not is_safe and self._guardrails.block_on_input:
            # Agent ran but we must block - discard response
            return AgentResponse(
                message=Message(role=Role.ASSISTANT, content=""),
                blocked=True,
                guardrail_result=GuardrailResult(
                    safe=False,
                    flagged_at="input",
                    input_classifier=input_classifier,
                    input_policy=input_policy,
                ),
            )

        # Safe or block_on_input=False - return with guard results
        return self._attach_input_guard_results(
            agent_response, input_classifier, input_policy
        )

    async def _execute_agent_core_with_session(
        self,
        messages: list[Message],
        session: AgentSession,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute the agent LLM and tool loop using session's cached clients.

        This is the session-aware version of _execute_agent_core. Instead of
        creating fresh clients, it uses the session's client cache.

        Args:
            messages: Conversation history (without system message).
            session: The session providing cached clients.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.

        Raises:
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        # Get fallback state from session
        fallback_state = session._fallback_state

        # Determine which model to try first - check if we should retry main
        if fallback_state.using_fallback and self._should_retry_main(fallback_state):
            logger.info(
                ErrorMessages.FALLBACK_RETRY_MAIN.format(
                    main_model=self._model.value,
                    successful_calls=fallback_state.successful_fallback_calls,
                )
            )
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0

        # If using fallback (sticky), try fallback first
        if fallback_state.using_fallback:
            return await self._execute_with_fallback_model_session(
                full_messages, session, fallback_state, response_format
            )

        # Try main model
        try:
            client = session._get_or_create_client(self._model.provider)
            response = await self._execute_with_client(
                client=client,
                model=self._model,
                full_messages=full_messages,
                response_format=response_format,
            )
            # Success on main - reset fallback state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return response
        except Exception as e:
            main_error = str(e)
            # No fallback configured - raise immediately
            if self._fallback is None:
                raise ModelFailedError(
                    model=self._model.value,
                    error=main_error,
                    has_fallback=False,
                ) from e

            # Try fallback
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._model.value,
                    to_model=self._fallback.model.value,
                    reason=main_error,
                )
            )
            try:
                fallback_client = session._get_or_create_client(
                    self._fallback.model.provider
                )
                response = await self._execute_with_client(
                    client=fallback_client,
                    model=self._fallback.model,
                    full_messages=full_messages,
                    response_format=response_format,
                )
                # Success on fallback - update state
                fallback_state.using_fallback = True
                fallback_state.successful_fallback_calls = 1
                return response
            except Exception as fallback_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=main_error,
                    fallback_model=self._fallback.model.value,
                    fallback_error=str(fallback_e),
                ) from fallback_e

    async def _execute_with_fallback_model_session(
        self,
        full_messages: list[Message],
        session: AgentSession,
        fallback_state: FallbackState,
        response_format: ResponseFormat | None = None,
    ) -> AgentResponse:
        """Execute with fallback model using session (sticky mode).

        Args:
            full_messages: Full conversation with system message prepended.
            session: The session providing cached clients.
            fallback_state: Fallback state tracking.
            response_format: Optional structured output configuration.

        Returns:
            AgentResponse with the final message and execution details.

        Raises:
            FallbackExhaustedError: If both models fail.
        """
        if self._fallback is None:
            raise ModelFailedError(
                model=self._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )

        try:
            client = session._get_or_create_client(self._fallback.model.provider)
            response = await self._execute_with_client(
                client=client,
                model=self._fallback.model,
                full_messages=full_messages,
                response_format=response_format,
            )
            # Success - increment counter
            fallback_state.successful_fallback_calls += 1
            return response
        except Exception as e:
            # Fallback failed - try main as last resort
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._fallback.model.value,
                    to_model=self._model.value,
                    reason=str(e),
                )
            )
            fallback_error = str(e)
            try:
                main_client = session._get_or_create_client(self._model.provider)
                response = await self._execute_with_client(
                    client=main_client,
                    model=self._model,
                    full_messages=full_messages,
                    response_format=response_format,
                )
                # Main recovered - reset state
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
                return response
            except Exception as main_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=str(main_e),
                    fallback_model=self._fallback.model.value,
                    fallback_error=fallback_error,
                ) from main_e

    def _run_streaming_with_session(
        self,
        messages: list[Message],
        session: AgentSession,
    ) -> AsyncIterator[str]:
        """Execute agent with streaming using session's cached clients.

        This is the session-aware version of _run_streaming.

        Args:
            messages: Conversation history (without system message).
            session: The session providing cached clients.

        Returns:
            AsyncIterator yielding SSE-formatted strings.
        """
        return self._run_streaming_with_session_impl(messages, session)

    async def _run_streaming_with_session_impl(
        self,
        messages: list[Message],
        session: AgentSession,
    ) -> AsyncIterator[str]:
        """Implementation of streaming with session support.

        Args:
            messages: Conversation history (without system message).
            session: The session providing cached clients.

        Yields:
            SSE-formatted strings for tool calls, tool results, content, blocked, done.
        """
        # Determine if we need to run input guardrails
        has_input_guard = (
            self._guardrails is not None
            and self._guardrails.input_mode != GuardrailMode.NONE
        )
        user_content = self._extract_user_content(messages) if has_input_guard else ""

        # Start guard task in background if needed
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ) = None
        if has_input_guard and user_content:
            guard_task = asyncio.create_task(
                self._check_guardrails(user_content, "input")
            )

        # Stream the agent response, checking guard status periodically
        async for sse in self._stream_agent_with_guard_and_session(
            messages, guard_task, session
        ):
            yield sse

    async def _stream_agent_with_guard_and_session(
        self,
        messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        session: AgentSession,
    ) -> AsyncIterator[str]:
        """Stream agent response using session's cached clients.

        This is the session-aware version of _stream_agent_with_guard.

        Args:
            messages: Conversation history (without system message).
            guard_task: Background guard task to monitor (or None if no guard).
            session: The session providing cached clients.

        Yields:
            SSE-formatted strings with sequence and created_at metadata.

        Raises:
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        # Prepend system message
        full_messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            *messages,
        ]

        # Create emitter once for the entire streaming session
        emitter = SSEEventEmitter()

        # Get fallback state from session
        fallback_state = session._fallback_state

        # Determine which model to try first - check if we should retry main
        if fallback_state.using_fallback and self._should_retry_main(fallback_state):
            logger.info(
                ErrorMessages.FALLBACK_RETRY_MAIN.format(
                    main_model=self._model.value,
                    successful_calls=fallback_state.successful_fallback_calls,
                )
            )
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0

        # If using fallback (sticky), try fallback first
        if fallback_state.using_fallback:
            async for sse in self._stream_with_fallback_model_session(
                full_messages, guard_task, emitter, session, fallback_state
            ):
                yield sse
            return

        # Try main model
        try:
            client = session._get_or_create_client(self._model.provider)
            async for sse in self._stream_with_client(
                client=client,
                model=self._model,
                full_messages=full_messages,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Success on main - reset fallback state
            fallback_state.using_fallback = False
            fallback_state.successful_fallback_calls = 0
            return
        except Exception as e:
            main_error = str(e)
            # No fallback configured - raise immediately
            if self._fallback is None:
                raise ModelFailedError(
                    model=self._model.value,
                    error=main_error,
                    has_fallback=False,
                ) from e

            # Try fallback
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._model.value,
                    to_model=self._fallback.model.value,
                    reason=main_error,
                )
            )
            try:
                fallback_client = session._get_or_create_client(
                    self._fallback.model.provider
                )
                async for sse in self._stream_with_client(
                    client=fallback_client,
                    model=self._fallback.model,
                    full_messages=full_messages,
                    guard_task=guard_task,
                    emitter=emitter,
                ):
                    yield sse
                # Success on fallback - update state
                fallback_state.using_fallback = True
                fallback_state.successful_fallback_calls = 1
                return
            except Exception as fallback_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=main_error,
                    fallback_model=self._fallback.model.value,
                    fallback_error=str(fallback_e),
                ) from fallback_e

    async def _stream_with_fallback_model_session(
        self,
        full_messages: list[Message],
        guard_task: (
            asyncio.Task[tuple[bool, ClassifierResult | None, PolicyResult | None]]
            | None
        ),
        emitter: SSEEventEmitter,
        session: AgentSession,
        fallback_state: FallbackState,
    ) -> AsyncIterator[str]:
        """Stream with fallback model using session (sticky mode).

        Args:
            full_messages: Full conversation with system message prepended.
            guard_task: Background guard task to monitor (or None).
            emitter: SSE event emitter for metadata.
            session: The session providing cached clients.
            fallback_state: Fallback state tracking.

        Yields:
            SSE-formatted strings.

        Raises:
            FallbackExhaustedError: If both models fail.
        """
        if self._fallback is None:
            raise ModelFailedError(
                model=self._model.value,
                error="No fallback configured but fallback_state.using_fallback=True",
                has_fallback=False,
            )

        try:
            client = session._get_or_create_client(self._fallback.model.provider)
            async for sse in self._stream_with_client(
                client=client,
                model=self._fallback.model,
                full_messages=full_messages,
                guard_task=guard_task,
                emitter=emitter,
            ):
                yield sse
            # Success - increment counter
            fallback_state.successful_fallback_calls += 1
            return
        except Exception as e:
            # Fallback failed - try main as last resort
            logger.warning(
                ErrorMessages.FALLBACK_TRIGGERED.format(
                    from_model=self._fallback.model.value,
                    to_model=self._model.value,
                    reason=str(e),
                )
            )
            fallback_error = str(e)
            try:
                main_client = session._get_or_create_client(self._model.provider)
                async for sse in self._stream_with_client(
                    client=main_client,
                    model=self._model,
                    full_messages=full_messages,
                    guard_task=guard_task,
                    emitter=emitter,
                ):
                    yield sse
                # Main recovered - reset state
                fallback_state.using_fallback = False
                fallback_state.successful_fallback_calls = 0
                return
            except Exception as main_e:
                raise FallbackExhaustedError(
                    main_model=self._model.value,
                    main_error=str(main_e),
                    fallback_model=self._fallback.model.value,
                    fallback_error=fallback_error,
                ) from main_e
