"""A local model through the OpenAI-compatible door, with no key at all.

Usage:
    llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0 --jinja -c 32768
    neosian chat --agent examples/local_agent.py

`api_key_env=None` declares an endpoint that signs nothing (`neosian docs
local`): the router hands the SDK a placeholder and never a key of yours,
so nothing in the environment is read or sent. The card is priced at zero,
never left unpriced, so cost lines read 0 µ$ rather than unknown; the
effort knob is off because the server takes no `reasoning_effort`.
"""

from neosian import AgentConfig, ModelPricing, OpenAICompatible, register_model

LOCAL = OpenAICompatible(
    name="local",
    api_key_env=None,
    base_url="http://127.0.0.1:8080/v1",  # Ollama serves on :11434/v1
    reasoning_effort=False,
    reasoning_field="reasoning_content",
)

GEMMA = register_model(
    "gemma-4-e4b-it",  # Ollama's name for the same weights: gemma4:e4b
    provider=LOCAL,
    context_window=32_768,  # the server's `-c`
    max_output_tokens=8_192,
    pricing=ModelPricing(input_per_mtok=0, output_per_mtok=0),
)

configuration = AgentConfig(
    system_prompt="You are a concise assistant.",
    model=GEMMA,
)
