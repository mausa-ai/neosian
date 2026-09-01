"""The tool-approval gate (DESIGN §17): a human decides every tool call.

`ToolGateConfig(approver)` intercepts each call before it runs, builtins
included, with complete authority. A denial is an in-band corrective
result the model adapts to, never an exception; default-deny is code,
not configuration — a timeout (60 s by default; `None` waits), an
approver exception, or a malformed return all deny naming the cause. On
the streaming path the wait shows as `tool_progress` frames. This
approver asks on the console, off the event loop so heartbeats keep
ticking.

Usage:
    ANTHROPIC_API_KEY=... python examples/approval_gate_example.py
    printf 'y\\nn\\n' | ANTHROPIC_API_KEY=... python examples/approval_gate_example.py
"""

import asyncio
import json
from datetime import UTC, datetime

from neosian import (
    Agent,
    AgentConfig,
    Message,
    Model,
    Role,
    Tool,
    ToolApprovalRequest,
    ToolDecision,
    ToolGateConfig,
    ToolResult,
)


@Tool(name="get_time", description="The current UTC time.")
async def get_time() -> ToolResult[str]:
    return ToolResult(
        success=True, data=datetime.now(UTC).isoformat(timespec="seconds")
    )


@Tool(name="send_email", description="Send an email to a colleague.")
async def send_email(to: str, subject: str, body: str) -> ToolResult[str]:
    return ToolResult(
        success=True, data=f"sent to {to}: {subject!r} ({len(body)} chars)"
    )


async def console_approver(request: ToolApprovalRequest) -> ToolDecision:
    print(f"\n[gate] {request.name}({json.dumps(request.arguments)})")
    answer = await asyncio.to_thread(input, "[gate] approve? [y/N] ")
    approved = answer.strip().lower() == "y"
    return ToolDecision(
        approved=approved, reason=None if approved else "declined at the console"
    )


agent = Agent(
    AgentConfig(
        system_prompt="You are a helpful assistant with tools. Use them when asked.",
        model=Model.CLAUDE_SONNET_5,
        tools=[get_time, send_email],
        tool_gate=ToolGateConfig(approver=console_approver, timeout_seconds=None),
        enable_todo=False,
    )
)


async def main() -> None:
    for prompt in (
        "What time is it right now?",
        "Email ada@lumen.example, subject 'Deploy moved', saying the deploy moved "
        "to Wednesday.",
    ):
        print(f"\nuser: {prompt}")
        response = await agent.run(
            [Message(role=Role.USER, content=prompt)], stream=False
        )
        for result in response.tool_results:
            verdict = "ok" if result.success else "denied"
            print(f"[tool] {verdict}: {result.data or result.error}")
        print(f"agent: {response.message.content}")


if __name__ == "__main__":
    asyncio.run(main())
