"""Agent core: configuration, the run funnel, and the session seam.

The orchestration lives in sibling modules (blocking, stream_run, loop,
stream_loop, stream_final, guards, fallback, tool_exec, emit), all keyed
off the per-run RunContext (DESIGN §3, N0 session-twin collapse).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from neosian._foundation.agent.session import AgentSession

from neosian._foundation.agent.blocking import run_blocking
from neosian._foundation.agent.context import RunContext, UsageLedger
from neosian._foundation.agent.emit import emit_turn
from neosian._foundation.agent.events import AgentEvent
from neosian._foundation.agent.guards import require_model_key
from neosian._foundation.agent.hooks import HookRunner
from neosian._foundation.agent.response import AgentResponse
from neosian._foundation.agent.stream_run import run_streaming
from neosian._foundation.agent.tool_scope import ToolScope, resolve_scope
from neosian._foundation.llm.base import BaseLLMClient, Message, ToolDefinition
from neosian._foundation.llm.router import ProviderRouter
from neosian._foundation.memory.skills import create_skill_tools
from neosian._foundation.memory.tools import create_memory_tool
from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    GuardrailStreamingError,
    StructuredOutputStreamingError,
)
from neosian._foundation.shared.registry import resolve_model
from neosian._foundation.shared.types import (
    AgentConfig,
    AnyModel,
    FallbackState,
    Provider,
    ResponseFormat,
    ToolChoice,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import (
    get_tool_definition,
    get_tool_metadata,
)
from neosian._foundation.tools.builtin.todo import update_todo

logger = logging.getLogger(__name__)

_DUPLICATE_TOOL_NAME = (
    "Tool name '{name}' is already registered by {earlier}; "
    "two tools cannot share a name"
)


def _origin(tool: ToolFunction) -> str:
    """Who registered a tool: a bridged tool's origin (an MCP server, §25),
    else the function's qualified name."""
    metadata = get_tool_metadata(tool)
    if metadata is not None and metadata.origin is not None:
        return metadata.origin
    return f"{tool.__module__}.{tool.__qualname__}"


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

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the agent from its configuration — every knob lives
        on `AgentConfig` (DESIGN §3)."""
        # Initialize router for provider management
        assert config.max_retries is not None  # Set by AgentConfig.__post_init__
        self._config = config
        self._router = ProviderRouter(
            max_retries=config.max_retries, timeout=config.timeout_seconds
        )
        self._client_factory = config.client_factory
        self._hooks = HookRunner(config.hooks)

        # Model and fallback configuration
        self._model = resolve_model(config.model)
        self._fallback = config.fallback
        # The fallback ladder, resolved once like the main row (§31).
        # `_fallback_model` is its first rung — what every one-rung
        # reader meant before the ladder existed (NC9, ledger #223).
        self._fallback_models: tuple[AnyModel, ...] = (
            ()
            if config.fallback is None
            else tuple(resolve_model(rung) for rung in config.fallback.models)
        )
        self._fallback_model: AnyModel | None = (
            self._fallback_models[0] if self._fallback_models else None
        )
        self._system_prompt = config.system_prompt
        self._max_tool_iterations = config.max_tool_iterations
        self._reasoning_effort = config.reasoning_effort
        assert config.max_output_tokens is not None  # Set by AgentConfig.__post_init__
        self._max_output_tokens = config.max_output_tokens
        self._cache_conversation = config.cache_conversation
        self._cache_ttl = config.cache_ttl
        self._max_tool_result_chars = config.max_tool_result_chars
        self._stream_tool_arguments = config.stream_tool_arguments
        self._server_compaction = config.server_compaction
        assert config.max_parallel_tools is not None  # Set by AgentConfig.__post_init__
        self._max_parallel_tools = config.max_parallel_tools
        self._context_policy = config.context_policy
        # The run's spend ceilings; enforced on the run's usage ledger.
        self._max_cost_micro_usd = config.max_cost_micro_usd
        self._max_total_tokens = config.max_total_tokens
        # The tool-approval gate (DESIGN §17): checked in execute_tool,
        # the leaf both paths share — parity by construction.
        self._tool_gate = config.tool_gate

        # Store guardrails config. The policy model defaults to the agent's
        # own (ledger #84); its client rides the run's acquire seam like
        # every other client (cached and closed by a session's pool — an
        # Agent mints nothing here), so client_factory injection makes
        # guardrails keylessly testable on FakeProvider. Key absence is
        # loud here, never a silent fail-open at check time.
        self._guardrails = config.guardrails
        self._guardrail_model: AnyModel | None = None
        if self._guardrails is not None:
            self._guardrail_model = resolve_model(
                self._guardrails.model or config.model
            )
            if self._client_factory is None:
                require_model_key(self._guardrail_model)

        # Build tool registry from decorated functions
        self._tools: dict[ToolName, ToolFunction] = {}
        self._tool_definitions: list[ToolDefinition] = []

        # Add global todo tool if enabled
        if config.enable_todo:
            self._register_tool(update_todo)

        # Register the memory tool if memory is configured
        if config.memory is not None:
            self._register_tool(
                create_memory_tool(config.memory, native=config.native_memory)
            )
        # Skill tools over the directory skills and the memory mounts'
        # `skills/` documents (§24): wherever the memory tool is, they are.
        if config.skills or config.memory is not None:
            for skill_tool in create_skill_tools(config.skills, config.memory):
                self._register_tool(skill_tool)
        if config.native_memory and self._model.provider is not Provider.ANTHROPIC:
            logger.warning(
                "native_memory=True is inert on %s — the memory_20250818 "
                "declaration is Anthropic-only; other providers receive "
                "the ordinary function schema",
                self._model.provider.value,
            )

        # Register user-provided tools
        for tool_func in config.tools:
            self._register_tool(tool_func)

    @property
    def config(self) -> AgentConfig:
        """The configuration this agent was built from — an Agent knows its
        configuration, never its history (DESIGN §3). Mutating it afterwards
        does not affect the agent; `Conversation` derives its own from it."""
        return self._config

    @property
    def max_tool_iterations(self) -> int:
        """The tool-loop bound, off the configuration."""
        return self._max_tool_iterations

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
        earlier = self._tools.get(metadata.name)
        if earlier is not None:
            raise ConfigurationError(
                _DUPLICATE_TOOL_NAME.format(
                    name=metadata.name, earlier=_origin(earlier)
                )
            )
        self._tools[metadata.name] = tool_func
        self._tool_definitions.append(definition)

    def _create_client(self, model: AnyModel) -> BaseLLMClient:
        """Create a client for model, honoring the configured factory.

        A factory sees the model itself — a registered door is told apart
        by `.door`, never collapsed to a provider row (DESIGN §2, §19).
        """
        if self._client_factory is not None:
            return self._client_factory(model)
        return self._router.create_client_for(model)

    def _validate_run(
        self, *, stream: bool, response_format: ResponseFormat | None
    ) -> None:
        """Run-entry guards, shared verbatim by Agent.run and AgentSession.run.

        The session twin skipping these was a defect class, not a variant
        (DESIGN §3 found-bug register #1). What tools and a schema may do
        together is `resolve_scope`'s to refuse, not these guards' (#225).
        """
        # Structured outputs require blocking mode: the streaming seam has
        # no response_format, and a schema needs the whole reply to validate.
        if response_format is not None and stream:
            raise StructuredOutputStreamingError()

        # Output guardrails require blocking mode
        if (
            stream
            and self._guardrails is not None
            and self._guardrails.has_output_guardrails
        ):
            raise GuardrailStreamingError()

    def _ledger(self) -> UsageLedger:
        """A fresh ledger carrying this run's spend ceilings (ledger #222).

        The budget lives on the ledger because the ledger is where every
        billed call in a run is folded — both paths, every fallback leg,
        and the guardrail classifier's own call.
        """
        return UsageLedger(
            max_cost_micro_usd=self._max_cost_micro_usd,
            max_total_tokens=self._max_total_tokens,
            cache_ttl=self._cache_ttl,
        )

    def _run_context(
        self, session: AgentSession | None, scope: ToolScope
    ) -> RunContext:
        """Build the per-run context — the seam the session twins collapsed into."""
        if session is None:
            return RunContext(
                agent=self,
                acquire=self._create_client,
                hooks=self._hooks,
                started=time.monotonic(),
                ledger=self._ledger(),
                scope=scope,
            )
        return RunContext(
            agent=self,
            acquire=session._get_or_create_client,
            hooks=self._hooks,
            fallback_state=session._fallback_state,
            started=time.monotonic(),
            ledger=self._ledger(),
            scope=scope,
        )

    async def _dispatch(
        self,
        messages: list[Message],
        *,
        stream: bool,
        response_format: ResponseFormat | None,
        tools: list[str | ToolFunction] | None,
        tool_choice: ToolChoice | None,
        session: AgentSession | None,
    ) -> AgentResponse | AsyncIterator[AgentEvent]:
        """The one entry funnel behind Agent.run and AgentSession.run.

        A single call site for the guards means neither entry point can
        skip them by construction (DESIGN §3 found-bug register #1). The
        tool scope is resolved here too, once, and rides the context the
        way the ledger does — neither driver resolves anything (#224).
        """
        self._validate_run(stream=stream, response_format=response_format)
        ctx = self._run_context(
            session,
            resolve_scope(
                self,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            ),
        )
        if stream:
            return run_streaming(ctx, messages)
        response = await run_blocking(ctx, messages)
        # The one blocking on_turn site: every path through _run_blocking
        # (finalized, input-blocked, guard-merged) funnels through here.
        await emit_turn(ctx, response, streamed=False)
        return response

    @overload
    async def run(
        self,
        messages: list[Message],
        *,
        stream: Literal[False],
        response_format: ResponseFormat | None = None,
        tools: list[str | ToolFunction] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AgentResponse: ...

    @overload
    async def run(
        self,
        messages: list[Message],
        *,
        stream: Literal[True],
        response_format: None = None,
        tools: list[str | ToolFunction] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def run(
        self,
        messages: list[Message],
        *,
        stream: bool,
        response_format: ResponseFormat | None = None,
        tools: list[str | ToolFunction] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AgentResponse | AsyncIterator[AgentEvent]:
        """Execute the agent with the given conversation history.

        Args:
            messages: Conversation history (without system message).
            stream: If True, yields typed AgentEvent values (DESIGN §6);
                sse_stream() relays them as SSE. If False, returns
                AgentResponse.
            response_format: Optional structured output configuration. When provided,
                the agent returns a structured response matching the Pydantic schema.
                Incompatible with stream=True. With tools in play the schema
                rides a synthetic final tool (NC9 #225).
            tools: The registered tools this run may call, by name or by
                decorated function; None is all of them, [] is none.
            tool_choice: Whether the model may, must, or must not call one
                this turn (NC9 #224).

        Returns:
            AgentResponse when stream=False, AsyncIterator[AgentEvent]
            when stream=True. The streaming iterator still raises on
            failure — a relaying host converts exceptions with
            ErrorEvent.from_exception.

        Raises:
            GuardrailStreamingError: If stream=True with output guardrails configured.
            StructuredOutputStreamingError: If stream=True with response_format.
            StructuredOutputToolsError: If response_format rides a tool_choice
                that forces some other tool.
            ConfigurationError: If tools names an unregistered tool, or
                tool_choice forces one this run does not send.
            ModelFailedError: If model fails and no fallback is configured.
            FallbackExhaustedError: If both main and fallback models fail.
        """
        return await self._dispatch(
            messages,
            stream=stream,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            session=None,
        )

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
