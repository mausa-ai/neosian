"""A model neosian has never heard of, through the OpenAI-compatible door.

Usage:
    EXAMPLE_API_KEY=... neosian playground examples/custom_model_agent.py

`register_model` is configuration: it runs once at import, beside the
agent it serves. The door names the endpoint and the env var that signs
requests to it; the registered model carries its own window, output
ceiling and µ$ prices, so cost lines, fallback gates and the context
policy treat it exactly like a shipped `Model`.
"""

from neosian import AgentConfig, ModelPricing, OpenAICompatible, register_model

EXAMPLE = OpenAICompatible(
    name="example",
    api_key_env="EXAMPLE_API_KEY",
    base_url="https://api.example.com/v1",
    temperature=True,
)

EXAMPLE_LARGE = register_model(
    "example-large",
    provider=EXAMPLE,
    context_window=131_072,
    max_output_tokens=16_384,
    pricing=ModelPricing(input_per_mtok=500_000, output_per_mtok=1_500_000),
)

configuration = AgentConfig(
    system_prompt="You are a concise assistant.",
    model=EXAMPLE_LARGE,
)
