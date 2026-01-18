"""Minimal agent for latency testing - no tools, no guardrails.

Usage:
    neosian playground examples/minimal_agent.py
"""

from neosian import AgentConfig, Model

# Agent configuration - export as 'configuration'
configuration = AgentConfig(
    system_prompt="You are a helpful assistant. Be concise.",
    tools=[],
    model=Model.GPT_OSS_20B,
    enable_todo=False,  # Disable built-in todo tool
    # No guardrails
)
