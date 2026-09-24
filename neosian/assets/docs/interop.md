---
title: "Interop: neosian memory under your own agent"
summary: The memory tool under pydantic-ai, the Agents SDK or an MCP client; the data stays yours
---

# Interop

neosian's memory is a tool, and a tool is two things: a definition (a
name, a description, a JSON Schema) and an executor. `tool_definition`
returns the first as a copy the caller owns; the tool itself is the
second. Any agent loop that can carry a function tool can carry it, so
the memory an agent built with neosian curates is the same memory a
pydantic-ai or OpenAI Agents SDK agent curates: one definition, any
loop, one directory underneath.

```python
from neosian import (
    FileStore, MemoryConfig, Mount,
    create_memory_tool, memory_system_section, tool_definition,
)

config = MemoryConfig(
    store=FileStore("memory"),
    mounts=[Mount(scope="user:demo", mount_path="user", description="Facts about the user")],
)
memory = create_memory_tool(config, actor="pydantic-ai:demo")
definition = tool_definition(memory)                 # name, description, parameters
instructions = await memory_system_section(config)   # the prompt pack plus the index
result = await memory(command="create", path="/user/tea", content="takes tea")
result.to_json()   # '{"success": true, "data": "Created /user/tea (v1)"}'
```

Three facts carry over. The instructions are the memory section every
neosian agent gets (`neosian docs memory`): the prompt pack and the
index, frozen for the conversation, rendered once at the start. The
executor takes the arguments the framework parsed as keywords and returns
a `ToolResult`; its JSON envelope is the string to hand back, and an
unknown command or a missing argument comes back inside it as a
corrective failure the model repairs. A mistyped value raises instead:
the neosian loop validates arguments before it calls a tool, other loops
may not, so the two examples catch and return `ToolResult.fail`. The
`actor` names the writer on every version row in the `kind:id` grammar
`neosian audit` reads (`neosian docs cli`).

## pydantic-ai

`examples/interop_pydantic_ai.py`: `Tool.from_schema` takes the
definition as it is and calls the function with keywords only, skipping
its own schema validation.

```python
tool = Tool.from_schema(
    call,                                   # awaits memory(**arguments), returns result.to_json()
    name=definition.name,
    description=definition.description,
    json_schema=definition.parameters,
)
agent = Agent(model, instructions=instructions, tools=[tool])
```

## The OpenAI Agents SDK

`examples/interop_openai_agents.py`: a `FunctionTool` carries the same
three fields and an `on_invoke_tool` that receives the arguments as a
JSON string.

```python
tool = FunctionTool(
    name=definition.name,
    description=definition.description,
    params_json_schema=definition.parameters,
    on_invoke_tool=on_invoke,               # json.loads, then memory(**arguments)
    strict_json_schema=False,
)
agent = Agent(name="memory", instructions=instructions, tools=[tool], model=model)
```

`strict_json_schema=False` keeps the schema the model sees identical to
the definition: the memory tool's optional parameters are nullable with
a `null` default, and strict mode would make every one of them required
and drop the defaults. neosian's own `Tool(strict=True)` produces the
strict shape from the source when a caller asks (`neosian docs tools`).

## Over MCP, any language

The same definition is served over the Model Context Protocol:
`python -m neosian.mcp --root memory --scope user:demo` on stdio, or the
state process's `/mcp` (`neosian docs mcp`). Both frameworks consume an
MCP server natively, as does every other client that speaks the
protocol, so a process that is not Python reaches the same memory
without the import.

## Running it

The unit tier runs both examples with no key, each driven by its
framework's own scripted model (`uv run pytest tests/unit/examples`).
A real model is named on the command line and found through the
environment alone:

```bash
OPENAI_API_KEY=... uv run python examples/interop_pydantic_ai.py "Remember that I take my tea without milk." openai:gpt-5.6-sol
OPENAI_API_KEY=... uv run python examples/interop_openai_agents.py "Remember that I take my tea without milk." gpt-5.6-sol
```

A local server takes `OPENAI_BASE_URL=http://127.0.0.1:8080/v1` with a
model name the server knows and any value for the key, which the server
ignores (`neosian docs local`). The frameworks are the `examples`
dependency group, never a dependency of the wheel.

## Your data

After one turn the directory is the record:

```
memory/
  user%3Ademo/
    documents/tea.md        # frontmatter and the text, as written
    versions/tea.jsonl      # one row per version, the actor on each
```

Every file is markdown or JSON lines you can `cat`, `grep`, diff and
commit; `neosian memory view /user/tea --root memory --mount
scope=user:demo,path=user` reads it back with the same tool the agents
used, and `neosian memory versions` the history. The store moves whole:
`neosian export` writes a directory that is itself a FileStore root,
`import` lands it in Postgres or behind the state process, and the
reverse leg comes back byte for byte (`neosian docs memory`). One writer
per root at a time, any number of readers (`neosian docs topology`).
