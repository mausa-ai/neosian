"""The tool layer's errors: the in-band `tool_` family (DESIGN §5, §27.9)."""

from __future__ import annotations

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError


class McpConnectionError(NeosianError):
    """An MCP server could not be connected or listed when `McpServer`
    was entered — a spawn, handshake or transport failure (DESIGN §25).
    Per-call failures never raise: they are in-band `ToolResult.fail`s.
    The first code of the reserved `tool_` family (ECOSYSTEM §6).
    """

    code = "tool_mcp_connection_failed"

    def __init__(self, server: str, error: BaseException) -> None:
        reason = str(error) or type(error).__name__
        super().__init__(
            f"MCP server '{server}' could not be connected: {reason}",
            details={"server": server, "error": reason},
        )
        self.server = server


class ToolInvalidArgumentsError(NeosianError):
    """A tool call's arguments did not bind (or, from NF slice B, did not
    validate); never raised — `ToolResult.code` carries the code in-band
    so the model can repair the call (NF #171, TG-40)."""

    code = "tool_invalid_arguments"

    def __init__(self, tool_name: str, error: str) -> None:
        super().__init__(
            ErrorMessages.TOOL_INVALID_ARGUMENTS.format(
                tool_name=tool_name, error=error
            ),
            details={"tool_name": tool_name, "error": error},
        )


class ToolExecutionError(NeosianError):
    """The tool body raised; the `ToolResult` twin of that failure."""

    code = "tool_execution_failed"

    def __init__(self, tool_name: str, error: str) -> None:
        super().__init__(
            ErrorMessages.TOOL_EXECUTION_FAILED.format(
                tool_name=tool_name, error=error
            ),
            details={"tool_name": tool_name, "error": error},
        )
