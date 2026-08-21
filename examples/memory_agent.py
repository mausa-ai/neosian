"""Memory-enabled agent — the N1 cross-session dogfood.

Usage:
    neosian playground examples/memory_agent.py

Tell it something worth remembering, /exit, relaunch, and ask about it:
the agent consults its memory through the `memory` tool, and the index
of what it knows is injected into the system prompt at startup. Memory
lives under the gitignored .neosian/memory directory — read it with cat.
"""

from pathlib import Path

from neosian import AgentConfig, FileStore, MemoryConfig, Model, Mount

_STORE_ROOT = Path(__file__).resolve().parent.parent / ".neosian" / "memory"

configuration = AgentConfig(
    system_prompt=(
        "You are a helpful assistant with persistent memory. Record facts "
        "worth keeping and consult your memory before answering questions "
        "about earlier conversations."
    ),
    model=Model.CEREBRAS_GPT_OSS_120B,
    enable_todo=False,
    memory=MemoryConfig(
        store=FileStore(_STORE_ROOT),
        mounts=(
            Mount(
                scope="user:demo",
                mount_path="user",
                description="durable facts about the user",
            ),
            Mount(
                scope="user:demo/proj:neosian",
                mount_path="project",
                description="facts about the current project",
            ),
        ),
    ),
)
