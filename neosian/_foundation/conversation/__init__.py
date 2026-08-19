"""The Conversation layer (DESIGN §9): the opt-in stateful shell.

Deliberately empty of imports: `memory/file.py` mixes `FileTurnStore` in,
and an importing `__init__` here would drag the agent into that chain and
break the memory ↛ provider-internals layering contract (DESIGN §1).
"""
