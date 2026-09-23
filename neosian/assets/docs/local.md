---
title: "Local serving: llama.cpp and Ollama on the door"
summary: A door that signs nothing, the zero card, the two recipes and the measured local row
---

# Local serving

A local server speaks the OpenAI wire and signs nothing, so it enters
through the same door as any model neosian has not shipped
(`neosian docs quickstart`), declared keyless:

```python
from neosian import AgentConfig, ModelPricing, OpenAICompatible, register_model

LOCAL = OpenAICompatible(
    name="local",
    api_key_env=None,                      # signs nothing
    base_url="http://127.0.0.1:8080/v1",   # llama-server; Ollama is :11434/v1
    reasoning_effort=False,                # the server takes no effort level
    reasoning_field="reasoning_content",   # where llama.cpp puts thinking
)
GEMMA = register_model(
    "gemma-4-e4b-it",
    provider=LOCAL,
    context_window=32_768,                 # the server's `-c`
    max_output_tokens=8_192,
    pricing=ModelPricing(input_per_mtok=0, output_per_mtok=0),
)
configuration = AgentConfig(system_prompt="You are a concise assistant.", model=GEMMA)
```

`api_key_env=None` is a statement, not a default: a keyless door must
name its `base_url`, the SDK's own endpoint is never keyless. The router
reads no environment variable for it and sends the SDK a fixed
placeholder in place of a key, because the SDK given no key at all would
fall back to `OPENAI_API_KEY` and ship it to the local server. Guardrails
on a keyless door need no key at construction; `neosian configure` and
`status` list no row for it. A server that does take a key (llama-server
with `--api-key`, a gateway) is an ordinary door with an `api_key_env`.

The card is priced at zero rather than left unpriced: an unpriced model
reports its cost as unknown (`None`) and, under a budget cap, warns once
per run; a zero card prices every token at 0 µ$, which is the truth of a
machine you own. `examples/local_agent.py` is the file above.

## llama.cpp

```bash
brew install llama.cpp        # or the release binaries at github.com/ggml-org/llama.cpp
llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0 --jinja -c 32768
neosian chat --agent examples/local_agent.py
```

`-hf` pulls the GGUF from Hugging Face into the local cache on first
start; `--jinja` turns the model's own chat template on, which is what
carries tool calls on this wire; `-c 32768` is the context the registered
model states. Gemma 4 thinks by default, and on this server its thoughts
arrive as `reasoning_content` (the door's `reasoning_field`), a few
hundred tokens a turn on a model that generates a few dozen a second.
Keep it: on the shipped memory pack's function transport, thinking on
scored 9 of 10 cells and thinking off (the template's own switch,
`--chat-template-kwargs '{"enable_thinking": false}'`) 4 of 10, the
model answering instead of calling the memory tool. The door is the
measured shape on llama.cpp 0.4.1: `reasoning_effort=False` because the
server takes no effort level (an asked effort is dropped with one
warning; thinking is the template's, not a request parameter);
`strict_schemas` stays on, the server honours a JSON schema by grammar.
Without a GPU expect a few tokens per second on a 4B model: the memory
pack's turns run to minutes.

## Ollama

```bash
ollama pull gemma4:e4b
neosian chat --agent examples/local_agent.py   # base_url="http://127.0.0.1:11434/v1"
```

The same weights under Ollama's own HTTP layer (`/v1` is its
OpenAI-compatible surface): the door is the one above with the port
changed and the model registered under Ollama's name for it,
`gemma4:e4b` (its Q4_K_M, 9.6 GB as Ollama packs it). Ollama's default
context is short; set `OLLAMA_CONTEXT_LENGTH=32768` so the window the
registered model states is the one served. Measured on Ollama 0.34.3:
the system prompt, a tool round trip, streamed content and a streamed
tool call answer the probes, structured output does not, the endpoint
ignoring `response_format` for this model on both modes and returning a
fenced block the validator refuses. So the Ollama recipe is chat and
tools; reflection and maintenance need `json_schema`, and the board was
measured on llama-server alone (`neosian docs baselines`).

## The shell

`neosian chat --agent FILE` and `neosian playground FILE` take the file's
model, so a keyless door opens with nothing configured. The resident chat
(`neosian chat` with no file) resolves its model before any file loads
and so never reaches a registered door; name one in a file.

vLLM, LM Studio and any other server on the wire enter the same way: the
door's `wire="chat"` is what they serve, and their knobs are theirs to
state (`neosian docs quickstart` lists them). Only the two recipes above
are measured.

## Measured

The `local` lane of the external tier measures Gemma 4 E4B on the shipped
memory pack, like every door row (`neosian docs baselines`): with a
llama-server up, `NEOSIAN_TEST_LOCAL_URL=http://127.0.0.1:8080/v1 make
test-external provider=local` runs the door probes, the catalog probe and
the board, one board per transport so each fits the lane's budget.
`scripts/local_server.sh` starts the server in docker for a machine
without llama.cpp, and CI's `local` matrix entry runs it on a CPU-only
runner, where the cells the budget cuts are recorded `timeout` reds. A
local row is a candidate by construction: no card to seal and no fixed
endpoint, so it never becomes a `Model` member. The first board, on a
laptop with a GPU (2026-09-23): 35 of 40 cells, function 9, cli 8, http 9
and mcp 9 of 10, every board inside its budget, the reds the skill
written without its frontmatter and one over-written fact.
