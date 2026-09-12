# Changelog

All notable changes to neosian are recorded here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/) from v1.0.0 (ECOSYSTEM §11). Release tags are
annotated `v<X.Y.Z>`; `[Unreleased]` accumulates during a phase and the
phase close names the version.

## [Unreleased]

### Added

- **A budget stop.** `AgentConfig` gains `max_cost_micro_usd` and
  `max_total_tokens`; a run that crosses either raises the new
  `BudgetExceededError` (`agent_budget_exceeded`, never retryable)
  carrying `kind`, `limit` and `spent` plus the billed usage. Both
  default to `None`. The ceiling lives on the run's usage ledger, the
  one place every billed call is folded, so it covers both paths, every
  fallback rung and the guardrail classifier's call with no parity to
  keep. Nothing is billed past the cap: the run stops on the call that
  crossed it, and a crossed budget never buys a fallback attempt, since
  the ceiling is the run's and no other model can fix it. A model
  registered without pricing cannot count against the cost ceiling and
  says so once per run at `WARNING`; `max_total_tokens` is the rail that
  fires on every model (DESIGN §3, ledger #222).
- **A fallback ladder.** `FallbackConfig` takes `models=[...]` beside
  the single `model=`, never both; a run walks the rungs in order until
  one answers. After construction `models` is the ordered tuple and
  `model` its first rung, so a one-rung ladder is the single fallback
  unchanged. Sticky state follows the rung that answered
  (`FallbackState.fallback_index`), and a sticky rung that fails keeps
  walking down before the main model's last try. A rung that cannot
  carry the conversation's media leaves the ladder rather than being
  attempted; a context overflow still raises rather than walking down
  into smaller windows. `FallbackExhaustedError` gains `attempts` —
  every `(model, error)` in trial order — while `main_*`/`fallback_*`
  keep naming the main model and a fallback rung, and the two-rung
  message is unchanged (DESIGN §3, ledger #223).

## [1.0.0rc6] - 2026-09-12

The shipped pages read without an em dash.

### Changed

- The ten shipped docs pages title themselves with a colon, not an em
  dash (`Memory: the file school`); the values are quoted YAML, so a
  title carrying a `: ` survives the frontmatter round trip.
- Their bodies drop the em dash too: roughly 450 of them become a
  colon, a comma or a pair of parentheses, sentence by sentence. The
  48 empty-table-cell markers stay. `baselines.md`'s Fingerprints rows
  read `` `path`: sha256 ``, and the gate that reads them follows.
- `llms.txt` and its twin read the same way: no em dash in the front
  door either.

## [1.0.0rc5] - 2026-09-11

Cerebras rides the OpenAI wire; one implementation of that wire ships.

### Changed

- The two Cerebras rows (`gpt-oss-120b`, `qwen-3.8-27b`) are served
  through a shipped `OpenAICompatible` door on the generic client, like
  xAI and Gemini (DESIGN §19.3, ledger #218). `Provider.CEREBRAS` stays
  their provider row; `ProviderRouter.create_client_for` serves them and
  the provider-keyed `create_client(Provider.CEREBRAS)` refuses, naming
  it. The door gains two knobs the move earned: `reasoning_format` (sent
  in `extra_body` beside a reasoning effort) and `retry_temperature`
  (the value resent on a tool-call 400 when a temperature was in play).
- The OpenAI-compatible client reads both tool-error body shapes (the
  nested `error.code` and Cerebras's flat `tool_use_failed`), raises
  `ProviderError` on an in-band error frame mid-stream, and reads a
  null token count as zero.
- The installer's closing lines name the human path: `neosian setup
  --write`, `neosian status`, bare `neosian`; the two per-client install
  verbs stay for agents behind `setup`.

### Removed

- `cerebras-cloud-sdk` leaves the dependency list; the Cerebras adapter
  and its converter are gone (536 lines).

## [1.0.0rc4] - 2026-09-11

The shell for humans and agents, and the catalog current at the promise.

### Added

- The shell for humans and agents (NY, DESIGN §30): `neosian status`
  (is this machine set up — the home, the keys by source, this
  directory's scopes, per client MCP / hooks / a resolving interpreter,
  the last recorded session, the install shape; exit 0, findings are
  data), `neosian setup [--write]` (both installers for every client
  found), `neosian chat [PROMPT] [--model M] [--agent FILE] [--resume ID]
  [--json]` (the resident agent that knows neosian — a `docs` tool over
  the shipped pages — on the home's project layout; one turn from a
  PROMPT or piped stdin, `--model fake` keyless) and bare `neosian`
  opening it on a terminal, `neosian update [--check|--write] [--mode
  off|notify|auto]` with the `[update] mode` knob (a PyPI check on the
  human verbs only), `NEOSIAN_SCOPE` as `--scope`'s environment twin,
  `eval --json`, the help grouped by audience.

- `Model.GROK_4_6` and `Model.GEMINI_3_7_FLASH`: every shipped row is a
  `Model` member, a door row carrying its `OpenAICompatible` door on
  `ModelSpec.door` / `Model.door` (NW1, DESIGN §31); `neosian.catalog`
  keeps the old names as aliases of the members.
- `lookup_model` is a root export, and `AgentConfig`, `FallbackConfig`,
  `GuardrailsConfig`, `CompactionConfig` and `ReflectionConfig` accept a
  wire id string for `model` — `AgentConfig(model="gpt-oss-120b")` builds
  the same agent as the member; an unknown id raises `InvalidModelError`.
- Rows, with their sealed cards (the providers' pages as read
  2026-09-10): `Model.GPT_5_6_SOL`, `GPT_5_6_TERRA`, `GPT_5_6_LUNA`
  (`reasoning_effort=MAX` passes through on them), `CLAUDE_FABLE_5_1`,
  `GEMINI_3_8_FLASH`, `CEREBRAS_QWEN_3_8_27B`.
- The catalog clock: `ModelSpec.retires` and `ModelSpec.card_until` on
  every shipped row, and a keyless test that fails inside 30 days of
  either (Haiku 4.5's floor is 2026-10-15; Gemini's introductory card
  holds through 2026-12-31).
- `OpenAICompatible.json_mode` (`"json_schema"` | `"json_object"`) and
  `OpenAICompatible.echo_reasoning`: a `json_object` door sends the plain
  JSON mode with the schema prepended to the system prompt, an echoing
  door sends `Message.reasoning` back under its `reasoning_field` on
  assistant turns (NW1, DESIGN §31.4). The `deepseek` and `qwen` lanes
  are back in the external tier as candidates; a door lane's board runs
  under an in-loop ceiling that records a cut cell as `timeout`.

### Changed

- With no `--scope`/`--mount`, every shell verb (`memory`, `audit`,
  `mcp`, `record`, `serve`'s `/mcp`) resolves the working directory's
  project layout — the pair the installers render — instead of refusing;
  a directory with no derived name still exits 2. `configure` is
  non-interactive and catalog-driven (`--list`, `--provider NAME --key -`
  from stdin, `--delete`, `--json`; prompts only bare on a terminal) and
  `config.toml` lives under the home (`NEOSIAN_HOME`). With `--json` in
  argv a grammar error (exit 2) also prints one `{"error": "usage",
  "hint"}` object on stdout. On a terminal `docs <topic>`, `audit`,
  `memory view /` and `status` render (markdown, a table, a tree); under
  a pipe, `NO_COLOR` or `--json` the bytes are unchanged.

### Removed

- `simple-term-menu`: the playground's pickers and the arena's save
  prompt ride `rich.prompt`; nothing in the shell is platform-bound.
- `Model.GPT_5_MINI`, `Model.GPT_5_NANO`, `Model.GPT_5_PRO` (OpenAI shuts
  the snapshots down 2026-12-11), `Model.CEREBRAS_GEMMA_4_31B` (off
  Cerebras's public endpoints since 2026-09-03) and
  `Model.CLAUDE_OPUS_4_6` (legacy, superseded by Opus 5). A retired id
  leaves in the next release after its provider's date (README,
  Stability).

### Changed

- `Model.CLAUDE_SONNET_5`'s card is $2/$10 (cache read $0.20, write
  $2.50): the announced 2026-09-01 rise did not occur. `PRICES_AS_OF` is
  2026-09-10; `DEFAULT_MODELS[Provider.OPENAI]` is `GPT_5_6_SOL`. The
  measured set of the external tier is Sonnet 5, Sol, gpt-oss-120b,
  qwen-3.8-27b, gemini-3.8-flash and grok-4.6; gpt-5.1 and Gemini 3.7 ride
  the catalog probe.
- The shipped door rows no longer register at import: `registered_models()`
  starts empty, and registering an id the enum ships is refused naming the
  member. `RegisteredModel` is `(value, spec)`; its `door` is the spec's.
- The memory baseline pack's two wordform pins (`long-horizon-recall`'s
  deploy branch, `write-discipline`'s Postgres version) widened to the
  fact, and a store-truth failure line now carries each live document's
  opening bytes; `baselines.md` gains a Known limits section (NZ).
- `project.urls` Documentation points at https://docs.neosian.com — the
  wheel's docs pages and `llms.txt`, rendered per release; the README
  carries the docs badge and `llms.txt` names the online copy.

## [1.0.0rc3] - 2026-09-09

`uv add neosian` is the whole product, short of a database server.

### Changed
- The core dependency list carries every door but one: the `neosian`
  shell, the MCP server and client, the state process and the
  OpenTelemetry exporter install with the bare package (about 70 MB);
  the imports stay lazy, so `import neosian` loads none of them. The
  Postgres driver stays the one extra, `neosian[postgres]`, with `[all]`
  as its alias (ledger #206).
- The install says what it brings: `scripts/install.sh` lists what lands
  and its size before installing, `neosian version` names the doors and
  whether the driver is present, and README, the quickstart page and
  `llms.txt` carry one paragraph on it. README's install block is one line.
- The playground's `--menu` and `--arena` pickers state their Unix-terminal
  requirement in their own help text.

### Deprecated
- The `cli`, `mcp`, `otel` and `server` extras are empty aliases for this
  release so pinned `neosian[extra]==…` lines still resolve; they are
  removed in the next release.

## [1.0.0rc2] - 2026-09-08

The first rc's own findings, fixed at a tag the README can point at.

### Fixed
- README's lockup follows GitHub's theme: a `<picture>` with a dark source
  (`branding/logo-dark.svg`) beside the ink one; the OS-scheme file had
  vanished on a dark GitHub page over a light OS.
- CI's installer job accepted only final versions in the banner check.
- `scripts/install.sh` pins the release it shipped with by default (`--version`
  still overrides): uv refuses an unpinned pre-release while any final
  release exists on the index, yanked or not, so the one-liner landed
  nothing during the rc period.

## [1.0.0rc1] - 2026-09-06

The first published release: the repository public under `mausa-ai`,
`neosian` on PyPI by trusted publishing, the container on GHCR. A
pre-release — pin it explicitly; the API stability promise rides v1.0.0.

### Added
- Published: `uv add "neosian==1.0.0rc1"` from PyPI and
  `ghcr.io/mausa-ai/neosian:1.0.0rc1` from GHCR; `curl -fsS
  https://neosian.com/install | bash` once the site serves the installer.
- README: two terminal recordings after the one screen — a Claude Code
  session recorded through the hooks, and the next session opening on
  where we left off; the VHS tapes sit beside them under `branding/readme/`.
- The sdist declares its contents: the planning documents, `.claude/`,
  `.github/`, `branding/` and the import-linter cache never ship.
- `CHANGELOG.md` — this file; `make release` refuses a version without its
  section.
- The container image is published to `ghcr.io/mausa-ai/neosian:<X.Y.Z>` on
  release tags (amd64 + arm64); PyPI by trusted publishing (NX).
- `scripts/install.sh` — the curl installer: it finds `uv` or installs it
  from a pinned release, runs `uv tool install "neosian[cli]"` and checks
  the PATH; `--find-links` and `--version` are accepted.
- `SECURITY.md` — where to report, and the supported line. The public
  floor is LICENSE, README and this file; the DCO sign-off lives in
  README's Development section.

### Changed
- The install form is the PyPI pin (`neosian[extra]==X.Y.Z`); the git-URL
  form and the "private repository" wording leave README, `llms.txt` and
  the quickstart page; README links and the lockup are absolute at the
  release tag (PyPI renders the README). The topology page's appliance
  quickstart runs the published image.
- `SERVICES.md` moves to the repository root and the memory baselines
  become a shipped docs page (`neosian docs baselines`, run ids as text);
  the fingerprint gate reads the page.
- The repository's one uv pin — the Dockerfile, the installer and both
  workflows — moves from 0.9.11 to 0.12.10.
- The README is rewritten for two readers — 566 lines down to 233, badges
  and the agent door on one screen, the keyless quickstart first. The
  `memory_write`, `revert_memory` and approval-gate passages moved onto the
  `memory` and `agent` docs pages.

### Removed
- `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` and the issue and PR templates:
  the DCO sign-off and the conduct line live in README.

## [0.90.0] - 2026-09-05

### Added
- The provider client seam is public: `BaseLLMClient`, `CompletionResponse`,
  `StreamChunk`, `ToolDefinition` and `ClientFactory` are exported, and
  `ClientFactory` is keyed by `AnyModel` so registered and built-in models
  come through one door.
- `AgentConfig` gains `max_tool_iterations`, `timeout_seconds` (threaded to
  every provider SDK constructor) and a typed `memory` field;
  `GuardrailsConfig` gains `timeout_seconds` and `block_on_output`;
  `ContextPolicy` gains an `estimator` hook for a real tokenizer.
- `iterations_exhausted` on `AgentResponse`, on `DoneEvent` and in its wire
  payload — running out of tool iterations is now observable (ECOSYSTEM §5).
- `AuthenticationError`, and `ToolResult.code` with
  `tool_invalid_arguments` / `tool_execution_failed` opening the reserved
  `tool_` code family; `details=` is back-filled on every
  argument-taking exception.
- `Message.extra` and `StreamChunk.extra` carry provider-specific fields
  through the codec and the Anthropic adapter, both paths (ledger #167).
- Two new docs pages, `agent` and `tools` (`uv run neosian docs agent`),
  covering every configuration knob and the per-provider constraint
  fidelity table.
- A `cli` extra: `typer`, `rich`, `simple-term-menu` and `tomli-w` install
  with `neosian[cli]`; the console script prints an install hint and exits
  1 when the extra is absent (ledger #182).

### Changed
- Breaking: tool schemas are generated by pydantic from the function
  signature — nested models, `datetime`, `UUID`, `$defs` and nullable
  optionals are described correctly, `Args:` docstrings become parameter
  descriptions, unannotated parameters are refused, and arguments are
  validated before the tool body runs (a failure returns a
  model-repairable `tool_invalid_arguments`). `strict: true` is emitted on
  the OpenAI and Cerebras wires (ledger #176–#181).
- Breaking: `Agent(max_tool_iterations=…)` moved onto
  `AgentConfig(max_tool_iterations=…)` with no compatibility shim.
- Breaking: the CLI dependencies left the core install — plain `pip install
  neosian` no longer brings `typer`/`rich`; the `python -m` doors stay core.
- Breaking: `python -m neosian.audit` is now `python -m neosian.ledger`;
  the module was renamed so `from neosian import audit` resolves to the
  exported function rather than a shadowing submodule.
- `AgentConfig.system_prompt` accepts a plain `str`; the `SystemPrompt`
  NewType is retired.
- `import neosian` loads no provider SDK — each client is imported inside
  its router branch (ledger #183).
- Provider SDK floors are capped at their next major, and the `psycopg`
  floor moves to 3.2.10 (the first release with a CPython 3.14 wheel).
- Anthropic tool results carry `is_error` when the tool failed.

## [0.89.0] - 2026-09-04

### Added
- `neosian export DIR` and `neosian import DIR` move a store's contents
  whole — memory scopes and conversations, with their version history —
  between any pair of shipped stores. The archive format is the `FileStore`
  layout itself, so one code path covers every pair (ledger #163–#166).
- `transfer(source, target)` as the library form, and a `Portable` protocol
  beside the ABCs (`scopes`, `conversations`, `restore_scope`,
  `restore_conversation`) implemented by `FileStore`, `PostgresStore` and
  `RemoteStore`; export itself runs privilege-free over the ABC reads.
- `ConversationArchive`, `ScopeArchive`, `TransferReport`, `UnitReport` and
  `ConversationConflictError` exported; `render_turn` and
  `render_projection` made public.
- Four `store/*` routes on the state process at `WIRE_VERSION` 3, so a move
  can cross the network.

### Changed
- The unit of a transfer is one scope or one conversation. A preflight
  refuses the whole run when a target unit is occupied (`target_occupied`,
  `agent_conversation_conflict`); each unit lands atomically, never merged
  and never overwritten.

### Fixed
- Postgres: `ORDER BY 1 COLLATE "C"` bound to the integer column in the
  portable listing — the union is now wrapped.

## [0.88.0] - 2026-09-04

### Added
- The agent consumes MCP servers as tools: `async with
  McpServer.stdio(...) as server` (also `.http(...)` and `.in_process(...)`)
  with `tools=[*server.tools]`. The caller owns the connection, the core
  stays stateless, and a bridged tool is an ordinary tool, so every
  existing seam applies (ledger #158–#161).
- Bare tool names by default with `prefix=` to disambiguate; a name
  collision is refused naming the server.
- `McpConnectionError` (`tool_mcp_connection_failed`) covers connecting
  alone. A server's `is_error` is relayed in band, text results are joined,
  `structured_content` arrives as data, and media results are marked.
- `ToolMetadata.origin` records which server a tool came from;
  `examples/mcp_client_example.py` shows the shape.
- `Transport.MCP` on the evaluation harness, driven by the official client.

## [0.87.0] - 2026-09-04

### Added
- Skills are documents: a skill is the document `skills/<name>` under any
  memory mount, so versions, receipts, revert, redaction and the audit
  trail are the store's, and `memory create` is the write path (ledger
  #154–#157).
- Skills are served over MCP both as read-only tools and as prompts (a
  slash command in Claude Code), from one factory for stdio and `/mcp`.
- The skill tools register wherever the memory tool does and read the store
  live; a `skills` docs page joins the manifest.

### Changed
- Breaking: `description` is the one required frontmatter key. `name` is
  optional and must match the address when present; `version` and `owner`
  are never read from frontmatter — the address is the name.
- `skill_duplicate_name` is retired to an unreachable shell: one mount
  cannot hold two skills under one name.

## [0.86.0] - 2026-09-04

### Added
- Link handles: long URLs, paths and ids in compacted history are replaced
  by `[link N]`, numbered by first appearance over the append-only turn
  history. The numbering is a pure function of the history, so nothing is
  stored and handles survive compaction (ledger #150–#153).
- Handles expand back to their full value inside tool-call arguments only;
  the model's own text is never rewritten. A handle into another
  conversation reads `[link <id>:N]`, numbered independently, and never
  expires.
- `LinkRegistry` on the `neosian.conversation` facade; `LINK_CHARS` is 48.

### Changed
- Clipping is atom-safe everywhere in the projection: a URL, path, id or
  handle is kept whole or dropped, never split mid-token. Measured
  keylessly at 483 → 223 estimated tokens on an eight-turn link-heavy view.

## [0.85.0] - 2026-09-04

### Added
- One home for every project and agent: `~/.neosian` (or `NEOSIAN_HOME`) is
  the store on every argv entry point when no flag names one (ledger
  #146–#149).
- `home()`, `project_scope()` and `project_mounts()` exported; both
  installers render a two-mount layout — `user:<login>` and
  `user:<login>/proj:<slug>`, the slug taken from the working directory's
  basename — into the client config they write.
- `neosian serve` and the `neosian record` spool default to the home.

### Changed
- Recorded foreign-agent sessions are filed under `/project`.

## [0.84.0] - 2026-09-03

### Added
- A session-start read verb: `neosian record` answers Claude Code's and
  Codex's `SessionStart` with where the work left off — the session's own
  record after a compaction, otherwise the three most recent, under one
  budget (ledger #141–#145).
- `recall_turn` over MCP: `create_memory_server(conversations=…)` serves a
  cross-conversation recall tool, so a foreign agent can read another
  session's turns; both entry points pass their store.
- The shipped evaluation pack gains a `cross-client` scenario.

## [0.83.2] - 2026-09-03

### Added
- OpenCode joins the client table: `neosian record install --client
  opencode` writes a plugin whole to
  `.opencode/plugins/neosian-record.js` from a shipped template, and the
  memory installer merges OpenCode's own entry shape into `opencode.json`
  (ledger #140).
- A conversation- or task-scoped `board` mount and read-only, log-projected
  views of another conversation: `Conversation(board=…, context=…)`,
  `ConversationView`, and `recall_turn(conversation=…)` over the views'
  allowlist. Views are frozen per instance and refreshed at the compaction
  boundary; the host decides what is shareable (ledger #136–#139).

### Removed
- Breaking: the blackboard is gone — `BlackboardProvider`,
  `FileBlackboard`, `BlackboardEntry`, `BlackboardName`,
  `BlackboardError`, the package and its three tools. Edit-only mounts and
  the new `board` mount subsume it; its error codes are kept as retired.

## [0.83.1] - 2026-09-03

### Added
- Codex joins the client table: `neosian record install --client codex`
  writes the project's `.codex/hooks.json` with a trust hint, and the
  memory installer prints the registration for Codex's own CLI to write
  (ledger #135). A headless `codex exec` needs
  `--dangerously-bypass-hook-trust` before hooks fire.


## [0.83.0] - 2026-09-03

### Added
- `neosian record` reads one agent hook payload on stdin and lands a
  foreign agent's session in the store as an ordinary conversation turn,
  authored `<agent>:<session_id>`, plus a `sessions/<session>` document
  (ledger #134).
- `neosian record install --client claude-code [--write]` writes the hook
  registration into the project's `.claude/settings.json`,
  key-preserving and idempotent.
- `neosian audit --scope S` — one ledger view over every substrate (files,
  Postgres, and the state process via `--url`): what was done, by whom and
  when, plus the redaction trail.
- An actor grammar every neosian writer speaks: `Actor`, `parse_actor`,
  `actor_matches`, `ACTOR_PATTERN`, `ACTOR_MAX_LENGTH`, plus `AuditEntry`,
  `MemoryRedaction` and `audit()`.
- Conversation turns record who appended them (an additive author field).
- The state process asserts who writes: `NEOSIAN_SERVE_TOKEN` accepts a
  per-client token table and the server prefixes the actor it records.
- An `agents` docs page carrying the per-agent client table.

## [0.82.1] - 2026-09-02

### Added
- `ReflectionResult.degraded` and `MaintenanceResult.degraded` name the
  failure when a model call degrades, leaving the affected turns pending
  for a later retry (ledger #128).

### Changed
- One request-body ceiling applies to every surface of the state process,
  `/mcp` included, replacing the per-route reader.
- A compacted transcript shares the payload budget with the rest of the
  request, and the fold latches once applied.
- DESIGN §7's promised prompt-registry override is dropped: prompts stay a
  pure read over a dict frozen at import, and the consumer seams are a
  custom `PolicyCategory` list, a user `@Tool` description, and the
  fingerprint-gated memory packs (ledger #129).

### Fixed
- Context-window overflow is classified before the tool retry on every
  client, at HTTP 400 and 413; a 429 "request too large" is non-retryable
  and is not an overflow.
- OpenAI-shaped wires: any terminal finish reason flushes accumulated tool
  calls, usage is read off whichever chunk carries it, a refusal becomes
  content with a `refusal` stop reason, and temperature is sent only when
  explicitly passed (`LLMDefaults.TEMPERATURE` is gone).
- Anthropic: tool results coalesce into one message and multiple system
  blocks join; truncated tool-call JSON names the stop reason and reports
  one `tool_arguments` error.
- Guardrail spend reaches the run's usage ledger; every nested stream
  closes with its consumer, and a shielded tool task no longer outlives
  cancellation.
- The store wire refuses a JSON number where document content belongs.
- Every timestamp neosian writes is tz-aware UTC; the console script boots
  without a TTY, and credential files are written `0600`.


## [0.82.0] - 2026-09-02

### Added
- xAI and Gemini ship as registry providers, promoted on two green
  dispatched baseline runs each: `neosian.catalog` exports `GROK_4_6` and
  `GEMINI_3_7_FLASH` (ledger #124).
- `CATALOG_SPECS` and the `neosian.catalog` facade give the shipped model
  rows one home.
- The CLI playground streams its replies.
- A demo tour: five new examples under `examples/` with the verbatim
  transcripts in `docs/tour/`.

### Changed
- Kimi remains a candidate; DeepSeek and Qwen leave the slate — their
  structured-output and tool-call behaviour did not pass the gate.

### Fixed
- A streamed tool turn that finishes with `stop` still delivers its
  accumulated tool calls on the OpenAI-compatible wire.
- Provider extras on tool calls round-trip through `ToolCall.extra`, so
  Gemini's thought signature survives every loop.

## [0.81.0] - 2026-09-01

### Added
- Models register through the OpenAI-compatible door: `register_model`,
  `OpenAICompatible`, `RegisteredModel` and `AnyModel` let a host add a
  model and its pricing without a new client, sharing one unchanged
  `ClientFactory` (ledger #118–#122).
- A global, write-once model registry; user-supplied prices sit outside the
  `PRICES_FINGERPRINT` gate, while shipped rows register at import.


## [0.80.0] - 2026-08-31

### Added
- The state process: `neosian serve` (and `python -m neosian.server`)
  serves memory and conversations over HTTP with MCP mounted at `/mcp`.
  New `server` extra (Starlette + uvicorn).
- `RemoteStore` implements both storage ABCs over the wire and passes both
  conformance kits against both backends. Capability is transmitted by an
  async `connect()` factory returning a per-capability class, so
  `type(store)` still answers (ledger #109).
- `build_app` on the `neosian.server` facade; twelve routes behind a
  `WIRE_VERSION` handshake whose `connect` refuses a skewed client.
- Bearer-token auth from `NEOSIAN_SERVE_TOKEN`, environment only; the
  process refuses to start when it is unset (ledger #110).
- A multi-stage, uv-locked `Dockerfile` with `/data` as the default root —
  built and smoked, deliberately not published (ledger #114); plus
  `make test-container`.
- `Transport.HTTP` on the evaluation harness — the fifth transport, so the
  shipped pack also runs over the wire (ledger #113).
- A `topology` docs page: when to embed the library and when to run the
  daemon, with the one-writer rule.

### Changed
- Breaking: playbooks are skills. `Playbook`, `PlaybookName`,
  `PlaybookLoadError`, `load_playbook`, `load_playbooks`,
  `AgentConfig.playbook_dir`, the `list_playbooks` / `load_playbook` tool
  names and all six `playbook_*` error codes are renamed to their `skill`
  spellings — total erasure, no alias and no deprecation window (ledger
  #112). The on-disk format is unchanged, so no user file moves.
- `RemoteStore` re-raises what `FileStore` raises: the envelope
  round-trips `code` and `details`, with `value_error` as the
  non-registry sentinel and no new error codes (ledger #111).
- The HTTP wire is deliberately not an ECOSYSTEM seam — skew is a version
  question, answered by `WIRE_VERSION` (ledger #108).

## [0.79.0] - 2026-08-23

### Added
- A tool-approval gate: `AgentConfig.tool_gate` takes one sync-or-async
  approver with complete authority over every tool call, checked inside
  `execute_tool` — the leaf both entry points share, so blocking and
  streaming behave identically. On the streaming path the pause is visible
  as `tool_progress` frames (ledger #105).
- `ToolApprovalRequest`, `ToolDecision` and `ToolGateConfig` exported.

### Changed
- A denial is in band: the run continues with a failed `ToolResult` the
  model can adapt to, `on_tool` fires with it, and no memory-write receipt
  (and so no `memory_write` event) is produced.
- Default-deny is fixed in code — a timeout (60 s by default, `None`
  waits), an approver exception and a malformed return all deny and name
  the cause. There is no fail-open switch (ledger #107).
- The event vocabulary stays at ten: no approval frame on the wire
  (ledger #106).

## [0.78.0] - 2026-08-22

### Added
- Edit-only mounts: `Mount(edit_only=True)` fixes the document set — the
  documents may be edited but not created, deleted or renamed (argv token
  `eo`, exclusive with `ro`), raising `MemoryEditOnlyMountError`
  (ledger #103).
- Three operator verbs on the memory CLI, all keyless: `versions PATH
  [--limit]` (the audit trail as text, full rows including content under
  `--json`), `redact PATH [--all]` (erasure; scope-wide only with the
  explicit flag) and `revert PATH --version N` (ledger #104). None is a
  model-reachable tool command.

### Changed
- The memory index marks edit-only mounts. Maintenance skips them whole and
  names its model-stage refusals; reflection sees them annotated and
  degrades a proposed create at the dispatcher.

## [0.77.0] - 2026-08-22

### Added
- A content-free `memory_write` event is emitted after the `tool_result`
  of every successful mutating memory command — ECOSYSTEM §5's event
  vocabulary grows from nine to ten. `MemoryWriteEvent` exported
  (ledger #99).
- `MemoryWriteReceipt` rides a `ToolResult` field that `to_json()`
  ignores, so all four transports' wire envelopes stay byte-identical
  (ledger #98).
- `revert_memory(config, path, version=, actor=)` — undo as an append, on
  the newest row only (`memory_conflict` / `revert_stale` otherwise),
  refusing redacted history (ledger #101).

### Changed
- Memory writes made inside a conversation are actored
  `<conversation_id>#<turn>`, resolved per command under the send lock
  (ledger #100).

## [0.76.0] - 2026-08-22

### Added
- The memory index pages at scale: hot document lines newest-updated
  first, per-directory fold lines and a mount-total floor, under a
  `budget_chars` keyword (8192 by default). The render is byte-identical
  under budget, deterministic across store instances, and every document
  stays named or covered — a 500-document root folds inside the budget
  (ledger #94).
- The evaluation harness gains seeded documents (`SeedDocument`, with an
  `age_days` backdate) and a `maintain:` session step (ledger #95–#96).

## [0.75.0] - 2026-08-22

### Added
- `run_maintenance` and `neosian memory maintain` — the gardener, explicit
  only, with no conversation rider. A deterministic stage merges
  byte-identical duplicates and prunes empty documents keylessly; `--model`
  adds a semantic pass that may also promote a document across mounts
  (ledger #90–#91).
- `MaintenanceResult` and `MaintenanceWrite` exported; spend rides the
  result.

### Changed
- Deletion is gated on `min_age` (7 days by default), redacted documents
  take no operation, and read-only mounts are structural (ledger #92).

## [0.74.0] - 2026-08-21

### Added
- `Conversation.reflect()` writes memory from a session's transcript in one
  structured call over every writable mount's live bodies, restricted to
  create/str_replace/delete and actored by the conversation id.
  `ReflectionConfig(enabled=True, model=None)` runs it as a default-on
  rider on `aclose()`, which now returns `ReflectionResult | None`
  (ledger #85–#88).
- `ReflectionConfig`, `ReflectionResult` and `ReflectionWrite` exported.

### Changed
- Reflection degrades rather than raises: a failed call leaves the pending
  turns for a later attempt, and a held send lock skips with a warning —
  the connection pool always closes. The frozen memory index is not
  refreshed, so the writes surface in the next conversation.

### Fixed
- The reflection schema is strict-mode compatible — per-command,
  all-required variants in a plain `anyOf` union, so OpenAI's strict
  structured output stops rejecting the call.

## [0.73.0] - 2026-08-21

### Removed
- Breaking: Groq exits the registry — the client module, the provider enum
  row, four model rows with their specs and pricing, the SDK dependency,
  the router branch and the CLI rows. Provider membership is now gated on
  two consecutive green dispatched baseline runs (ledger #84).

### Changed
- Breaking: `AgentConfig.model` defaults to `CEREBRAS_GPT_OSS_120B`.
- Breaking: the guardrail classifier no longer runs on a hard-wired
  safeguard model. `GuardrailsConfig.model` defaults to the agent's own
  model, the classifier runs through the ordinary client seam (so
  `client_factory` is honoured and guardrails are keylessly testable), no
  explicit temperature is sent, and a missing guardrail-provider key raises
  at construction rather than failing open.

## [0.72.0] - 2026-08-21

### Added
- OpenTelemetry: `neosian.otel.otel_hooks()` returns plain `AgentHooks`
  that emit flat post-hoc spans under the gen_ai semantic conventions —
  never message content, arguments or results. New `otel` extra, API only
  (ledger #83).
- `docs/BASELINES.md`: the first published per-provider memory baselines,
  methodology before numbers, with sha256 fingerprints of `memory.yaml`
  and the shipped pack gated by the unit suite.
- The shipped evaluation pack grows to six scenarios (contradiction,
  long-horizon recall, correct-wrong-memory) plus three negatives.

### Changed
- Document expectations accept a `path_prefix` region: exactly one live
  document under it satisfies `content`, two is a duplicate, and
  `versions`/`actions` apply to the match. Exact `path:` keeps its
  strictness (ledger #82).

## [0.71.0] - 2026-08-21

### Added
- `neosian memory <cmd> --scope SCOPE` puts the six memory commands on the
  shell — the fourth transport for one shared tool definition — with
  `--json` returning the envelope verbatim; `python -m neosian.memory` is
  the PATH-free twin (ledger #77).
- `neosian docs [topic]` prints the shipped documentation pages straight
  from the wheel: quickstart, memory, cli, mcp, topology.
- `neosian mcp install --client <c> [--write]` prints or applies an MCP
  client registration: a key-preserving merge that refuses an unparseable
  config, writes an absolute root, never writes the DSN, and refuses a
  missing client directory rather than creating it (ledger #80–#81).
- `llms.txt` at the repository root with a byte-identical twin shipped in
  the wheel; its install pin is tied to `__version__` (ledger #79).

### Changed
- Breaking: the Postgres DSN environment key for every argv entry point is
  `NEOSIAN_POSTGRES_DSN`, replacing `NEOSIAN_MCP_POSTGRES_DSN` with no
  alias (ledger #76).
- `FileStore` is documented as one writer per root (ledger #74).

## [0.70.0] - 2026-08-21

### Added
- The README rewritten to match the tree (16 sections): the opt-in stack,
  a `Conversation` quickstart, memory/storage/MCP snippets, the keyless
  `FakeProvider` section with `client_factory` injection, hooks,
  evaluation including `kind: memory`, and a Stability section naming the
  four public surfaces.
- Distribution metadata: description, keywords, classifiers and project
  URLs.

### Changed
- ECOSYSTEM §10 lists `ConversationStore` and `ConversationStoreContract`;
  §6 blesses the shipped `agent_conversation_*` codes and declines a
  `conversation_` prefix; §11 states that SemVer is guaranteed from
  v1.0.0 (ledger #71).
- The release first cut as `v1.0.0` was withdrawn and re-cut as `v0.70.0`
  before any consumer vendored it — a version number is a promise, and 1.0
  waits for a stronger surface (ledger #73).

## [0.69.0] - 2026-08-21

### Added
- `kind: memory` evaluation configs score a scenario against store truth,
  not against the response: `MemoryEvalConfig`, `MemoryScenario`,
  `MemorySession`, `DocumentExpectation`, `StoreExpectation` and
  `Transport` join the `neosian.evaluation` facade.
- The shipped pack `examples/eval_memory_baseline.yaml`, green with zero
  API keys.

### Changed
- Transports are the variants axis of a memory evaluation, and every red
  names the store root it ran against, so a failure is inspectable
  (ledger #64, #69).

## [0.68.0] - 2026-08-21

### Changed
- Breaking: the evaluation surface is facade-only. `EvalCase`,
  `EvalConfig`, `EvalResult`, `EvalTurn`, `Expectation`,
  `ToolCallCapture` and `TurnResult` leave the root `__all__` for
  `neosian.evaluation`, which exports 33 names.
- Breaking: evaluation configs are schema v2 — `kind:` from day one,
  strictly checked keys and models validated at load, with migration hints
  for the v1 spellings (`prompts:`, `mock_response:`, `on_success:`).
- Breaking: matchers are typed and strict — `bool` is not `1`, there is no
  `str()` coercion, `int`↔`float` is kept, and the first-call tool rule is
  enforced. Tools are stubbed by default, with `execute_tools` to opt in.
- `neosian eval` exits 1 on any failure and writes a schema-2 artifact;
  two new codes (`eval_config_unknown_key`, `eval_model_unknown`).

## [0.67.0] - 2026-08-20

### Added
- Memory over MCP: `python -m neosian.mcp --root DIR --scope user:me`
  serves the same `memory` tool over stdio from the same store, with the
  memory prompt pack as the server's instructions;
  `create_memory_server` on the new `neosian.mcp` facade and a `neosian
  mcp` CLI pass-through. New `mcp` extra (ledger #50–#52).
- Mounts on the command line as `--mount scope=…,path=…[,ro]`, with
  `--scope` as sugar for one read-write mount at `memories`; the Postgres
  DSN arrives by environment variable only, never on argv (ledger #53).

## [0.66.0] - 2026-08-20

### Added
- Anthropic server-side context compaction behind
  `AgentConfig.server_compaction`: `CompactionBlock` joins the
  `ContentBlock` union and round-trips through the codec, the streamed
  deltas and the assembled message, with its tokens folded into `Usage`
  on both paths (ledger #45–#48).
- `ModelSpec.supports_compaction_blocks` gates pre-flight and fallback;
  the OpenAI-compatible converters reject the block.

### Changed
- Assistant messages may now carry a list of text plus compaction blocks;
  media content in an assistant message still raises. `Conversation` warns
  under the flag, since a projection would drop the blocks (ledger #49).

## [0.65.0] - 2026-08-20

### Added
- Native Anthropic memory: `AgentConfig.native_memory` sends the
  schema-less `{"type": "memory_20250818", "name": "memory"}` tool
  definition instead of the function schema — a pure transport swap over
  the same store, with `cache_control` still valid on the native entry
  (ledger #41).
- The reference argument vocabulary ships first-class: the `file_text`
  alias and `view_range` slicing with real line numbers.

### Changed
- The flag never raises. It is inert with one warning per condition on
  non-Anthropic models, the wire description drops but
  `memory_system_section` stays verbatim, and no `ModelSpec` field or
  fallback gate is involved (ledger #42–#44).

## [0.64.0] - 2026-08-20

### Added
- Pool tuning on `PostgresStore(dsn, *, min_size=4, max_size=None,
  pool_timeout=30.0)`; passing nothing keeps psycopg's own defaults, and
  each value is guarded with a plain `ConfigurationError`.
- `examples/fastapi_chatbot.py` — two web workers on one conversation, and
  the reference implementation of the event relay: keepalive comments on
  the host's timer with the pending `__anext__` held in a persistent task
  (a `wait_for` would cancel into the agent's generator), a code-only
  `ErrorEvent` for a `NeosianError`, and `finally: cancel()` as the
  disconnect path.

## [0.63.0] - 2026-08-20

### Added
- `PostgresStore` implements both storage ABCs on one autocommit pool:
  single-statement CTE mutations, a primary-key retry that makes
  `expected_version` race-safe, and
  `supports_optimistic_concurrency=True`. The store never owns a
  transaction (ledger #34–#39).
- Shipped DDL: `apply_schema()` and `python -m neosian.schemas postgres`
  render an idempotent, `{{schema}}`-parameterized script with
  RLS-friendly ownership keys on every row.
- The first optional extra, `postgres`; core imports stay driver-free.
- `NEOSIAN_TEST_POSTGRES_DSN` and `make test-postgres` for the Postgres
  suite.

## [0.62.0] - 2026-08-20

### Added
- `Conversation` owns one lazy `AgentSession`, rebound rather than rebuilt
  at a compaction boundary so the client cache stays valid; public
  `aclose()` (idempotent) plus `__aenter__`/`__aexit__`, which do no I/O
  on entry. Sticky fallback now spans sends (ledger #33).

### Changed
- Breaking: the CLI playground is rebuilt on `Conversation`. Conversations
  resume by id against a store at `<cwd>/.neosian`; `--resume` refuses a
  path-shaped argument, and the per-session `Session` JSON file with its
  save-at-exit is gone.
- `--menu` model selection no longer silently drops nine configuration
  fields.

## [0.61.0] - 2026-08-20

### Added
- Compaction v1: `CompactionConfig` (default on) and `CompactionResult`.
  At a boundary, pending turns become model-written digests which fold
  into fixed-aligned epoch summaries in one batched write; public
  `compact()` runs regardless of `enabled`.
- `recall_turn` re-hydrates a folded turn verbatim
  (`create_recall_turn_tool`), and a rendered view carries
  `[recall_turn(n)]` pointers where it clipped.

### Changed
- Every distillation failure warns and degrades rather than raising; a
  failed fold is retried at the next boundary. Compaction spend folds into
  the conversation's usage (ledger #29).

## [0.60.0] - 2026-08-19

### Added
- `Conversation` — the opt-in persistence layer around the stateless core:
  send blocking or streaming persisting identical turns, resume by id, the
  memory tool rebound with `actor=<conversation_id>`, and a frozen memory
  index injected once (DESIGN §9).
- A separate `ConversationStore` ABC with store-assigned gapless turn
  numbers and projections in the ABC from day one; `FileStore` implements
  both seams, and `ConversationStoreContract` ships in
  `neosian.conversation.testing` (ledger #21, #23).
- The message codec is public — `message_to_json` and `message_from_json`
  (ledger #22) — alongside `ConversationTurn`, `ConversationProjection`,
  `parse_conversation_id` and `CONVERSATION_FORMAT_VERSION`.
- Three `agent_conversation_*` error codes (ledger #24).

### Changed
- `Agent.config` and `Agent.max_tool_iterations` are read-only properties.

### Fixed
- On the streaming path `on_turn` now fires before the terminal event at
  all three sites, so a consumer that has seen `done` holds the persisted
  turn.

## [0.58.0] - 2026-08-19

### Added
- One `memory` tool with a `command` enum — view, create, str_replace,
  insert, delete, rename — auto-registered by `AgentConfig.memory`
  (ledger #19). Unknown commands and missing arguments fail correctively
  rather than raising.
- `MemoryConfig` and `Mount(scope, mount_path, read_only, description)`:
  at least one mount, unique mount paths, and the first path segment picks
  the mount. `create_memory_tool` and `memory_system_section` exported.
- The memory prompt pack ships as `assets/prompts/memory.yaml`; the tool
  description stays under the 1024-character OpenAI-compatible cap.
- `examples/memory_agent.py`, and a playground that injects the memory
  section and builds the agent inside one event loop.

### Changed
- Every `MemoryStoreError` reaches the model as a corrective
  `ToolResult.fail("[code] message")` with a per-code hint.
- `view /` renders the memory index verbatim; documents are line-numbered
  and a redacted document is labelled rather than looking empty.

## [0.57.0] - 2026-08-19

### Added
- The memory storage seam: the `MemoryStore` ABC (seven abstract async
  methods, no `__init__`, no connection, no DDL), the frozen
  `MemoryDocument` / `MemoryEntry` / `MemoryVersion` types, and
  `MEMORY_FORMAT_VERSION`.
- `FileStore`, the reference implementation: an exact envelope codec, a
  JSONL sidecar as the version-counter truth, atomic same-directory
  writes, symlink containment, best-effort `expected_version`, and a
  `redactions.jsonl` erasure trail.
- The scope grammar (`Scope`, `parse_scope`, `SCOPE_PATTERN`,
  `SCOPE_MAX_LENGTH`) and the document-path grammar; a 512-character scope
  survives `NAME_MAX` by percent-encoding one directory per segment.
- `MemoryStoreContract`, the public conformance kit, in
  `neosian.memory.testing` — `pytest` is never a runtime dependency.
- Seven `memory_*` error codes under a base `MemoryStoreError`, plus the
  `Clock` protocol and `SystemClock`.

## [0.56.0] - 2026-08-19

### Added
- `ContextPolicy` — a default-on, deliberately underestimating character
  heuristic checked once per attempt before the call. Fallback to a
  model with a smaller window is refused, and caller-input errors
  (overflow, unsupported content) re-raise unwrapped with usage attached
  (ledger #16–#17).
- `event_schemas()` and `python -m neosian.schemas events [--out DIR]`;
  `sse_stream()` and `ErrorEvent.from_exception` for hosts that relay.
- The evaluation runner accepts a per-case `script:`, making eval runs
  keyless end to end, and rebuilds conversational context from
  `turn_messages`.
- `llm/codec.py` gives sessions full-fidelity save and load; the CLI
  playground persists the user message and `turn_messages` verbatim, and
  `--resume PATH` replays them.

### Changed
- Breaking: `run(stream=True)` returns `AsyncIterator[AgentEvent]` — nine
  frozen dataclasses in `agent/events.py`, stamped by one sequencer with
  `ReadyEvent` first, sequence numbers from 1 and continuity across
  fallback. Terminals carry `usage_by_model` and `DoneEvent.model` is the
  API-reported model. Payloads carry an `event` discriminator key
  (ledger #15).
- Breaking: the `heartbeat` event is renamed `tool_progress` and carries
  `elapsed_ms: int`.

### Removed
- Breaking: the old SSE layer is deleted — `streaming.py` with
  `SSEEventType`, `error_event`, `heartbeat_event` and `reasoning_event`.
  The library still raises on failure and never emits an error frame
  itself; hosts relay one via `ErrorEvent.from_exception`.

## [0.55.0] - 2026-08-19

### Added
- `AgentHooks` — `on_turn`, `on_llm_call`, `on_tool` and `on_fallback`,
  taking frozen event objects, sync or async, swallowed unless strict and
  awaited inline for sequence determinism; plus `TurnEvent`,
  `LlmCallEvent`, `ToolEvent` and `FallbackEvent`.
- `AgentResponse.turn_messages` — a replayable per-attempt message
  snapshot whose last element is the response message.
- `LLMError.usage` and `LLMError.usage_by_model`: a failed run carries its
  billed usage on both the blocking and the streaming path, replacing an
  internal smuggle. A blocked response keeps its billed usage.

### Changed
- `AgentResponse` is frozen with slots and tuples and gains
  `usage_by_model`; `StreamChunk.model` is populated by every client.
- The agent's two entry points collapse into one `_dispatch` funnel over a
  frozen `RunContext`, whose `acquire` is the client seam. The private
  `*_with_session` twins are gone, so a test that patched them moves to
  the sibling modules.

### Fixed
- Under fallback, the second attempt no longer inherits the failed main
  attempt's mutated history; the per-attempt usage ledger is inherited
  instead.


## [0.54.0] - 2026-08-18

### Added
- `neosian.fake` — `FakeClient`, `FakeScript`, `FakeTurn`, `FakeCall`,
  `StreamShape` and `FakeScriptExhaustedError`, with three FakeProvider
  models in the real registry (a deliberate capability split) and failure
  injection through the provider wrap, so the library is exercisable with
  zero API keys. `AgentConfig.client_factory` is honoured at every
  client-creation site.
- Machine-readable error codes: every exception carries `code`,
  `retryable` and `details` (47 codes, append-only), exposed as
  `ERROR_CODES` and `python -m neosian.schemas errors`. New
  `ContextWindowExceededError` and
  `ProviderError(provider, message, *, status, retryable, request_id)`,
  with `cause_code` and `provider_status` on terminal errors.
- `cost_micro_usd`, `format_micro_usd`, `MICRO_PER_USD`,
  `PRICES_FINGERPRINT` and `ModelUsage`.

### Changed
- Breaking: money is integer micro-USD with ceiling division.
  `ModelPricing` fields are int µ$/MTok, and `Usage` and `ModelPricing`
  are frozen with slots.
- Breaking: token classes are renamed `cache_read_tokens` and
  `cache_write_tokens`; the streamed usage keys follow the same
  vocabulary.
- Every shipped prompt — the guardrail classifier, six policies and six
  tool descriptions — moved to `assets/prompts/*.yaml` with `{{var}}`
  interpolation, loaded fail-fast at import.
- Every provider `complete`/`stream` body is wrapped, so a provider error
  arrives as a typed neosian error with its cause attached.

### Removed
- Breaking: the float `cost()` accessor is gone; use `cost_micro_usd`.

### Fixed
- Input-guardrail results no longer drop `model=` from the response.
- Both entry points share one `_validate_run`, so neither can skip a
  guard.

[Unreleased]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc5...HEAD
[1.0.0rc6]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc5...v1.0.0rc6
[1.0.0rc5]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc4...v1.0.0rc5
[1.0.0rc4]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc3...v1.0.0rc4
[1.0.0rc3]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc2...v1.0.0rc3
[1.0.0rc2]: https://github.com/mausa-ai/neosian/compare/v1.0.0rc1...v1.0.0rc2
[1.0.0rc1]: https://github.com/mausa-ai/neosian/compare/v0.90.0...v1.0.0rc1
[0.90.0]: https://github.com/mausa-ai/neosian/compare/v0.89.0...v0.90.0
[0.89.0]: https://github.com/mausa-ai/neosian/compare/v0.88.0...v0.89.0
[0.88.0]: https://github.com/mausa-ai/neosian/compare/v0.87.0...v0.88.0
[0.87.0]: https://github.com/mausa-ai/neosian/compare/v0.86.0...v0.87.0
[0.86.0]: https://github.com/mausa-ai/neosian/compare/v0.85.0...v0.86.0
[0.85.0]: https://github.com/mausa-ai/neosian/compare/v0.84.0...v0.85.0
[0.84.0]: https://github.com/mausa-ai/neosian/compare/v0.83.2...v0.84.0
[0.83.2]: https://github.com/mausa-ai/neosian/compare/v0.83.1...v0.83.2
[0.83.1]: https://github.com/mausa-ai/neosian/compare/v0.83.0...v0.83.1
[0.83.0]: https://github.com/mausa-ai/neosian/compare/v0.82.1...v0.83.0
[0.82.1]: https://github.com/mausa-ai/neosian/compare/v0.82.0...v0.82.1
[0.82.0]: https://github.com/mausa-ai/neosian/compare/v0.81.0...v0.82.0
[0.81.0]: https://github.com/mausa-ai/neosian/compare/v0.80.0...v0.81.0
[0.80.0]: https://github.com/mausa-ai/neosian/compare/v0.79.0...v0.80.0
[0.79.0]: https://github.com/mausa-ai/neosian/compare/v0.78.0...v0.79.0
[0.78.0]: https://github.com/mausa-ai/neosian/compare/v0.77.0...v0.78.0
[0.77.0]: https://github.com/mausa-ai/neosian/compare/v0.76.0...v0.77.0
[0.76.0]: https://github.com/mausa-ai/neosian/compare/v0.75.0...v0.76.0
[0.75.0]: https://github.com/mausa-ai/neosian/compare/v0.74.0...v0.75.0
[0.74.0]: https://github.com/mausa-ai/neosian/compare/v0.73.0...v0.74.0
[0.73.0]: https://github.com/mausa-ai/neosian/compare/v0.72.0...v0.73.0
[0.72.0]: https://github.com/mausa-ai/neosian/compare/v0.71.0...v0.72.0
[0.71.0]: https://github.com/mausa-ai/neosian/compare/v0.70.0...v0.71.0
[0.70.0]: https://github.com/mausa-ai/neosian/compare/v0.69.0...v0.70.0
[0.69.0]: https://github.com/mausa-ai/neosian/compare/v0.68.0...v0.69.0
[0.68.0]: https://github.com/mausa-ai/neosian/compare/v0.67.0...v0.68.0
[0.67.0]: https://github.com/mausa-ai/neosian/compare/v0.66.0...v0.67.0
[0.66.0]: https://github.com/mausa-ai/neosian/compare/v0.65.0...v0.66.0
[0.65.0]: https://github.com/mausa-ai/neosian/compare/v0.64.0...v0.65.0
[0.64.0]: https://github.com/mausa-ai/neosian/compare/v0.63.0...v0.64.0
[0.63.0]: https://github.com/mausa-ai/neosian/compare/v0.62.0...v0.63.0
[0.62.0]: https://github.com/mausa-ai/neosian/compare/v0.61.0...v0.62.0
[0.61.0]: https://github.com/mausa-ai/neosian/compare/v0.60.0...v0.61.0
[0.60.0]: https://github.com/mausa-ai/neosian/compare/v0.58.0...v0.60.0
[0.58.0]: https://github.com/mausa-ai/neosian/compare/v0.57.0...v0.58.0
[0.57.0]: https://github.com/mausa-ai/neosian/compare/v0.56.0...v0.57.0
[0.56.0]: https://github.com/mausa-ai/neosian/compare/v0.55.0...v0.56.0
[0.55.0]: https://github.com/mausa-ai/neosian/compare/v0.54.0...v0.55.0
[0.54.0]: https://github.com/mausa-ai/neosian/releases/tag/v0.54.0
