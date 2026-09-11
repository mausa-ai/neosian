---
title: Tools — the schema the model gets
summary: "@Tool: what the signature becomes on the wire, what is validated, what each provider keeps"
---

# Tools

A tool is an async function under `@Tool(name, description)`. Its
signature is the contract: one pydantic model is synthesised from the
parameters, its JSON Schema is what every provider receives, and the
same model validates every call before the body runs. The arguments a
tool receives are therefore the ones its signature promises — a nested
model as an instance, a `datetime` as a `datetime` — or the model gets
a failure it can repair.

```python
from datetime import datetime
from typing import Annotated
from pydantic import BaseModel
from neosian import Desc, Min, Tool, ToolResult

class Address(BaseModel):
    street: str
    zip: str

@Tool(name="ship", description="Ship an order")
async def ship(
    to: Address,
    when: datetime,
    qty: Annotated[int, Desc("Units to ship"), Min(1)] = 1,
    note: str | None = None,
) -> ToolResult[str]:
    """Ship it.

    Args:
        to: The delivery address.
        when: The pickup time, ISO 8601.
    """
    return ToolResult.ok(f"{qty} to {to.street} at {when:%H:%M}")
```

## What the signature becomes

| annotation | on the wire |
|---|---|
| `str`, `int`, `float`, `bool` | the JSON scalar |
| `list[T]`, `dict[str, T]`, `set[T]`, `tuple[A, B]` | `array` (`uniqueItems`, `prefixItems`) / `object` with the value schema |
| `Literal[...]`, an `Enum` | `enum` (the Enum under `$defs`) |
| `T | None` | `anyOf` with `null` — nullable, and still required when it has no default |
| `TypedDict`, `BaseModel`, `@dataclass` | a closed object under `$defs`, referenced by `$ref`; nesting and recursion work |
| `datetime`, `date`, `UUID`, `Decimal` | `string` with `format`, validated back into the type |
| `Any` | `{}` — anything; say so deliberately |
| a default | `default`, and the parameter leaves `required` |

Every object carries `additionalProperties: false`; no property carries
a `title`. What the schema cannot state truthfully is refused at
decoration with a `ConfigurationError`: a parameter without an
annotation, `*args`/`**kwargs`/positional-only parameters, a default
that is not JSON, a `params=` name the signature does not declare.

## Describing parameters

Three sources, in precedence order:

1. `Annotated[T, Desc("…")]` on the parameter.
2. `Tool(params={"name": "…"})` — prose kept outside the function; the
   builtins take theirs from the shipped YAML packs.
3. A Google-style `Args:` section in the docstring (`name: text`,
   `name (type): text`, continuation lines indented deeper).

## Constraints, and what each provider keeps

`Min`, `Max`, `MinLen`, `MaxLen`, `Pattern` are neosian's names;
pydantic's own vocabulary — `Field(ge=1, max_length=8)`,
`annotated_types.Gt(0)`, `StringConstraints` — is honoured beside them.
All of it lands in the schema and all of it is **enforced at the tool
boundary on every provider**; what differs is whether the model is told
up front:

| keyword | Anthropic | OpenAI wire |
|---|---|---|
| `minimum`, `maximum`, `exclusive*` | dropped on the wire, always | sent |
| `multipleOf`, `minLength`, `maxLength` | sent; dropped under `strict` | sent |
| `minItems`, `maxItems` | sent; dropped under `strict` when > 1 | sent |
| `pattern`, `format`, `enum`, `$defs` | sent | sent |

A model that ignores a constraint it was not told about sees the
validation failure and corrects the call on the next turn.

## Validation and repair

Before a tool runs, its arguments are validated against the model the
schema came from. Lax coercion applies (`"7"` becomes `7`); an unknown
key, a missing required parameter or a value outside its constraint is
returned to the model as `ToolResult.fail` with
`code: tool_invalid_arguments` and one line per failing field:

```json
{"success": false, "error": "Invalid arguments for tool 'ship': to.zip: Field required; qty: Input should be greater than or equal to 1", "code": "tool_invalid_arguments"}
```

The body never sees a call that did not validate. On Anthropic a failed
result also rides as `is_error: true` on the `tool_result` block. A
definition the library attached rather than built — an MCP server's
(`neosian docs mcp`) — has no local model: the core binds, the server
validates and answers in-band.

## Strict mode

`Tool(..., strict=True)` asks the provider to constrain decoding to the
schema. Anthropic honours it against a per-request complexity budget
(twenty strict tools). On the OpenAI wire the tool is sent
with `strict: true` in the strict-mode shape: every property required,
`null` defaults dropped, every object closed — so make optional
parameters nullable (`T | None`). A registered door with
`strict_schemas=False` sends the tool best-effort instead. Default off.

## Results

`ToolResult.ok(data)` or `ToolResult.fail(error, system_reminder=…,
code=…)`; the JSON envelope is what the model reads. The `tool_` codes
and the agent-side knobs (`max_parallel_tools`, `max_tool_iterations`,
the approval gate) are on the `agent` page (`neosian docs agent`).
