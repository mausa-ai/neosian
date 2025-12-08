"""Minimal agent for latency testing - no tools, no guardrails.

Usage:
    neosian playground examples/minimal_agent.py
"""

from neosian import AgentConfig

# Agent configuration - export as 'configuration'
configuration = AgentConfig(
    system_prompt="You are a helpful assistant. Be concise.",
    tools=[],
    provider="groq",
    enable_todo=False,  # Disable built-in todo tool
    # No guardrails
)