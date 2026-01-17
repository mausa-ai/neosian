"""Auto-mocking for tool execution during evaluation.

Wraps tools to return mock success responses while capturing call details.
"""

import functools
from typing import Any

from neosian._foundation.shared.constants import Evaluation
from neosian._foundation.shared.types import ToolCallCapture, ToolFunction
from neosian._foundation.tools.base import ToolResult, get_tool_metadata


def create_mock_tool(
    original_tool: ToolFunction,
    captures: list[ToolCallCapture],
) -> ToolFunction:
    """Create a mock version of a tool that captures calls.

    The mock:
    - Returns ToolResult.ok() with a generic success message
    - Captures the tool name and arguments for evaluation
    - Preserves the original tool metadata for LLM schema

    Args:
        original_tool: The original tool function to mock.
        captures: List to append captured calls to.

    Returns:
        Mock tool function with same metadata as original.
    """
    metadata = get_tool_metadata(original_tool)
    tool_name = metadata.name if metadata else original_tool.__name__

    @functools.wraps(original_tool)
    async def mock_wrapper(**kwargs: Any) -> ToolResult[dict[str, Any]]:
        # Capture the call
        captures.append(ToolCallCapture(name=tool_name, arguments=kwargs))

        # Return generic success
        return ToolResult.ok(
            {"status": "success", "message": Evaluation.MOCK_SUCCESS_MESSAGE}
        )

    # Preserve tool metadata for LLM
    if metadata:
        mock_wrapper._tool_metadata = metadata  # type: ignore[attr-defined]

    return mock_wrapper


def mock_agent_tools(
    agent: Any,
    captures: list[ToolCallCapture],
) -> None:
    """Replace all tools in an agent with mocked versions.

    Modifies agent._tools in place, replacing each tool function
    with a mock that captures calls and returns success.

    Args:
        agent: The Agent instance to mock.
        captures: List to append captured calls to.
    """
    for tool_name, tool_func in list(agent._tools.items()):
        agent._tools[tool_name] = create_mock_tool(tool_func, captures)
