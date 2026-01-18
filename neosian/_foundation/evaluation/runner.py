"""Evaluation runner.

Orchestrates running eval matrix: prompts × models × cases.

Supports two modes:
1. Legacy mode: prompts list contains Python agent files
2. Variant mode: agent is a Python file, prompts are YAML configs
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from neosian._cli.loader import load_agent_config
from neosian._cli.playground import _load_credentials_from_config
from neosian._foundation.agent.base import Agent
from neosian._foundation.evaluation.mocker import mock_agent_tools
from neosian._foundation.evaluation.prompt_config import load_prompt_config
from neosian._foundation.evaluation.scorer import score_turn
from neosian._foundation.llm.base import Message, Role, ToolDefinition
from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import (
    EvalCase,
    EvalConfig,
    EvalResult,
    Expectation,
    Model,
    PromptConfig,
    SystemPrompt,
    ToolCallCapture,
    ToolName,
    TurnResult,
)

logger = logging.getLogger(__name__)

# Progress callback: (prompt_idx, model_idx, case_idx, status, latency_ms)
# status: "running" | "passed" | "failed"
# latency_ms: response time (0.0 for "running" status)
ProgressCallback = Callable[[int, int, int, str, float], None]


class _FallbackDetector(logging.Handler):
    """Log handler that detects fallback events during agent execution."""

    def __init__(self) -> None:
        super().__init__()
        self.fallback_occurred = False
        self.fallback_reason: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        """Check if this log record indicates a fallback."""
        if "Falling back from" in record.getMessage():
            self.fallback_occurred = True
            self.fallback_reason = record.getMessage()


@contextmanager
def _detect_fallback() -> Iterator[_FallbackDetector]:
    """Context manager to detect fallback during agent execution.

    Usage:
        with _detect_fallback() as detector:
            response = await agent.run(messages, stream=False)
        if detector.fallback_occurred:
            # Handle fallback case
    """
    detector = _FallbackDetector()
    agent_logger = logging.getLogger("neosian._foundation.agent.base")
    agent_logger.addHandler(detector)
    try:
        yield detector
    finally:
        agent_logger.removeHandler(detector)


async def run_evaluation(
    config: EvalConfig,
    on_progress: ProgressCallback | None = None,
) -> list[EvalResult]:
    """Run full evaluation matrix.

    Iterates through all combinations of prompts × models × cases.

    Args:
        config: Evaluation configuration.
        on_progress: Optional callback for progress updates.
            Called with (prompt_idx, model_idx, case_idx, status).

    Returns:
        List of EvalResult for each combination.
    """
    # Load credentials from config file into environment
    _load_credentials_from_config()

    results: list[EvalResult] = []

    first_case = True
    for prompt_idx, prompt_file in enumerate(config.prompts):
        for model_idx, model in enumerate(config.models):
            for case_idx, case in enumerate(config.cases):
                # Throttle to avoid rate limits (skip delay for first case)
                if not first_case:
                    await asyncio.sleep(Evaluation.THROTTLE_DELAY_MS / 1000)
                first_case = False

                # Signal running
                if on_progress:
                    on_progress(prompt_idx, model_idx, case_idx, "running", 0.0)

                result = await _run_single_case(prompt_file, model, case, config.agent)
                results.append(result)

                # Signal result with latency
                if on_progress:
                    status = "passed" if result.passed else "failed"
                    on_progress(prompt_idx, model_idx, case_idx, status, result.latency_ms)

    return results


async def _run_single_case(
    prompt_file: str,
    model: str,
    case: EvalCase,
    agent_file: str | None = None,
) -> EvalResult:
    """Run evaluation for a single prompt × model × case combination.

    Args:
        prompt_file: Path to prompt config (YAML in variant mode, Python in legacy).
        model: Model identifier (provider:model format).
        case: The evaluation case.
        agent_file: Path to Python agent file (variant mode only).

    Returns:
        EvalResult with pass/fail and details.
    """
    try:
        # Parse model string to Model enum
        parsed_model = _parse_model(model)

        # Create agent based on mode
        if agent_file is not None:
            # Variant mode: agent from Python, prompts from YAML
            agent = _create_agent_with_prompt_config(
                agent_file, prompt_file, parsed_model
            )
        else:
            # Legacy mode: prompt_file is a Python agent file
            agent_config, _ = load_agent_config(prompt_file)
            agent_config.model = parsed_model
            agent_config.enable_todo = False
            agent = Agent(config=agent_config)

        # Run case
        if case.is_conversational:
            return await _run_conversational(prompt_file, model, case, agent)
        return await _run_one_shot(prompt_file, model, case, agent)

    except Exception as e:
        logger.warning(
            "Evaluation failed for %s × %s × %s: %s",
            prompt_file,
            model,
            case.name,
            str(e),
        )
        return EvalResult(
            case_name=case.name,
            prompt_file=prompt_file,
            model=model,
            passed=False,
            error=str(e),
        )


def _create_agent_with_prompt_config(
    agent_file: str,
    prompt_file: str,
    model: Model,
) -> Agent:
    """Create agent and apply prompt config overrides.

    Loads the Python agent for tool implementations, then overrides
    system_prompt and tool descriptions from the YAML prompt config.

    Args:
        agent_file: Path to Python agent file with tool implementations.
        prompt_file: Path to YAML prompt config file.
        model: Model to use.

    Returns:
        Agent with overridden prompts and descriptions.
    """
    # Load agent config from Python file
    agent_config, _ = load_agent_config(agent_file)
    agent_config.model = model
    agent_config.enable_todo = False

    # Load prompt config from YAML
    prompt_config = load_prompt_config(prompt_file)

    # Override system prompt
    agent_config.system_prompt = SystemPrompt(prompt_config.system_prompt)

    # Create agent
    agent = Agent(config=agent_config)

    # Apply tool description overrides
    _apply_tool_overrides(agent, prompt_config)

    return agent


def _apply_tool_overrides(agent: Agent, prompt_config: PromptConfig) -> None:
    """Apply tool description overrides from prompt config.

    Modifies agent's tool definitions in place to use descriptions
    from the YAML prompt config.

    Args:
        agent: The agent to modify.
        prompt_config: Prompt config with tool overrides.
    """
    for tool_name, tool_config in prompt_config.tools.items():
        # Find and update the tool definition
        for i, definition in enumerate(agent._tool_definitions):
            if definition.name == tool_name:
                # Create new definition with overridden description
                agent._tool_definitions[i] = ToolDefinition(
                    name=ToolName(tool_name),
                    description=tool_config.description,
                    parameters=definition.parameters,
                )
                break


async def _run_one_shot(
    prompt_file: str,
    model: str,
    case: EvalCase,
    agent: Agent,
) -> EvalResult:
    """Run a one-shot evaluation case.

    Args:
        prompt_file: Path to agent config file.
        model: Model identifier.
        case: The one-shot case.
        agent: Configured agent.

    Returns:
        EvalResult with single turn result.
    """
    # Setup mock and capture
    captures: list[ToolCallCapture] = []
    mock_agent_tools(agent, captures)

    # Run agent with timing and fallback detection
    messages = [Message(role=Role.USER, content=case.input or "")]
    start_time = time.perf_counter()
    with _detect_fallback() as detector:
        response = await agent.run(messages, stream=False)
    latency_ms = (time.perf_counter() - start_time) * 1000

    # If fallback occurred, mark as failed
    if detector.fallback_occurred:
        return EvalResult(
            case_name=case.name,
            prompt_file=prompt_file,
            model=model,
            passed=False,
            error=detector.fallback_reason,
            latency_ms=latency_ms,
        )

    # Score
    turn_result = score_turn(
        expectation=case.expect or _empty_expectation(),
        tool_calls=captures,
        response_content=response.message.content,
        turn_index=0,
    )

    return EvalResult(
        case_name=case.name,
        prompt_file=prompt_file,
        model=model,
        passed=turn_result.passed,
        turns=[turn_result],
        tool_sequence=[c.name for c in captures],
        latency_ms=latency_ms,
    )


async def _run_conversational(
    prompt_file: str,
    model: str,
    case: EvalCase,
    agent: Agent,
) -> EvalResult:
    """Run a conversational evaluation case.

    Builds context turn by turn, evaluating each turn.

    Args:
        prompt_file: Path to agent config file.
        model: Model identifier.
        case: The conversational case.
        agent: Configured agent.

    Returns:
        EvalResult with per-turn results.
    """
    messages: list[Message] = []
    all_turns: list[TurnResult] = []
    all_tool_names: list[str] = []
    all_passed = True
    total_latency_ms = 0.0

    for turn_idx, turn in enumerate(case.conversation or []):
        # Setup fresh capture for this turn
        captures: list[ToolCallCapture] = []
        mock_agent_tools(agent, captures)

        # Add user message
        messages.append(Message(role=Role.USER, content=turn.user))

        # Run agent with timing and fallback detection
        start_time = time.perf_counter()
        with _detect_fallback() as detector:
            response = await agent.run(messages, stream=False)
        total_latency_ms += (time.perf_counter() - start_time) * 1000

        # If fallback occurred, fail the entire case
        if detector.fallback_occurred:
            return EvalResult(
                case_name=case.name,
                prompt_file=prompt_file,
                model=model,
                passed=False,
                error=detector.fallback_reason,
                latency_ms=total_latency_ms,
            )

        # Score this turn
        turn_result = score_turn(
            expectation=turn.expect,
            tool_calls=captures,
            response_content=response.message.content,
            turn_index=turn_idx,
        )
        all_turns.append(turn_result)
        all_tool_names.extend(c.name for c in captures)

        if not turn_result.passed:
            all_passed = False

        # Add response to context for next turn
        messages.append(response.message)

        # Add tool results to context
        for tool_call in response.tool_calls_made:
            messages.append(
                Message(
                    role=Role.TOOL,
                    content='{"success": true}',
                    tool_call_id=tool_call.id,
                )
            )

    return EvalResult(
        case_name=case.name,
        prompt_file=prompt_file,
        model=model,
        passed=all_passed,
        turns=all_turns,
        tool_sequence=all_tool_names,
        latency_ms=total_latency_ms,
    )


def _parse_model(model_str: str) -> Model:
    """Parse model string into Model enum.

    Args:
        model_str: Model string, either:
            - provider:model format (e.g., "groq:llama-3.3-70b-versatile")
            - model name only (e.g., "llama-3.3-70b-versatile")

    Returns:
        Model enum value.

    Raises:
        ValueError: If model string doesn't match any Model enum value.
    """
    # Extract model name from provider:model format
    if ":" in model_str:
        _, model_name = model_str.split(":", 1)
    else:
        model_name = model_str

    # Look up in Model enum by value
    for m in Model:
        if m.value == model_name:
            return m

    raise ValueError(f"Unknown model: {model_name}")


def _empty_expectation() -> Expectation:
    """Create empty expectation for cases without expect block."""
    return Expectation()
