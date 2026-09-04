"""`ToolResult` — what a tool returns and what the model sees (DESIGN §3).

Split from `tools/base.py` at NF so the decorator module stays under the
size gate; `tools.base` re-exports the name.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from neosian._foundation.shared.serialization import safe_json_dumps

if TYPE_CHECKING:
    from neosian._foundation.memory.receipt import MemoryWriteReceipt


@dataclass
class ToolResult[T]:
    """Result from a tool execution.

    Tools return either success with data or error with message.
    Optionally includes a system_reminder for agent guidance.

    Attributes:
        success: Whether the tool execution succeeded.
        data: The result data on success.
        error: Error message on failure.
        system_reminder: Optional guidance for the agent (hints, caveats, follow-ups).
        receipt: Structured record of a successful memory mutation (NP).
            In-process only — `to_json()` never carries it, so the wire
            envelope is byte-identical with or without one.
        code: On failure, the machine code from the `tool_` family
            (`ERROR_CODES`) naming the failure class — in-band, so the
            model can repair a bad call (NF #171, TG-40). None when the
            tool itself failed without one.
    """

    success: bool
    data: T | None = None
    error: str | None = None
    system_reminder: str | None = None
    receipt: "MemoryWriteReceipt | None" = None
    code: str | None = None

    @classmethod
    def ok(
        cls,
        data: T,
        system_reminder: str | None = None,
        *,
        receipt: "MemoryWriteReceipt | None" = None,
    ) -> "ToolResult[T]":
        """Create a successful result.

        Args:
            data: The result data.
            system_reminder: Optional guidance for the agent.
            receipt: Structured memory-write record (in-process seam).
        """
        return cls(
            success=True, data=data, system_reminder=system_reminder, receipt=receipt
        )

    @classmethod
    def fail(
        cls,
        error: str,
        system_reminder: str | None = None,
        *,
        code: str | None = None,
    ) -> "ToolResult[T]":
        """Create a failed result.

        Args:
            error: Error message describing the failure.
            system_reminder: Optional guidance for the agent (e.g., retry hints).
            code: The `tool_` machine code for the failure class, if any.
        """
        return cls(
            success=False, error=error, system_reminder=system_reminder, code=code
        )

    def to_json(self) -> str:
        """Serialize to JSON string for LLM consumption.

        Deliberately excludes `receipt` — the wire envelope is frozen
        across transports (the CLI prints this verbatim, ledger #77) and
        the receipt is an in-process seam.

        Returns:
            JSON string with success/data/error, `code` on a coded failure,
            and optional system_reminder.
        """
        if self.success:
            output: dict[str, Any] = {"success": True, "data": self.data}
        else:
            output = {"success": False, "error": self.error}
            if self.code is not None:
                output["code"] = self.code

        if self.system_reminder:
            output["system_reminder"] = self.system_reminder

        return safe_json_dumps(output, "tool_result.data")
