"""Memory-enabled agent — the N1 cross-session dogfood.

Usage:
    neosian playground examples/memory_agent.py

Tell it something worth remembering, /exit, relaunch, and ask about it:
the agent consults its memory through the `memory` tool, and the index
of what it knows is injected into the system prompt at startup. Memory
lives in the home — `~/.neosian`, or `$NEOSIAN_HOME` — under the
project layout every neosian door shares (DESIGN §22): `user:<login>` at
/user and `user:<login>/proj:<slug>` at /project, the slug this
directory's name. Read it with cat; `neosian audit --scope` lists it.
"""

from neosian import AgentConfig, FileStore, MemoryConfig, Model, home, project_mounts

configuration = AgentConfig(
    system_prompt=(
        "You are a helpful assistant with persistent memory. Record facts "
        "worth keeping and consult your memory before answering questions "
        "about earlier conversations."
    ),
    model=Model.CEREBRAS_GPT_OSS_120B,
    enable_todo=False,
    memory=MemoryConfig(store=FileStore(home()), mounts=project_mounts()),
)
