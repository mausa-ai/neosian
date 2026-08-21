"""The agent under the memory eval — a bare config, deliberately memory-less.

The suite owns the store and the mounts (`mounts:` in the YAML; one
fresh store root per cell), so an agent config carrying `memory=` is
refused as a red cell. The harness wires the memory tool, the prompt
pack, and the per-session index exactly as `Conversation` would.

Run: neosian eval examples/eval_memory_baseline.yaml
"""

from neosian import AgentConfig, Model

configuration = AgentConfig(
    system_prompt=(
        "You are a helpful assistant with persistent memory. Record facts "
        "worth keeping and consult your memory before answering questions "
        "about earlier conversations."
    ),
    model=Model.CEREBRAS_GPT_OSS_120B,
    enable_todo=False,
)
