---
title: "Memory baselines: the published per-provider numbers"
summary: Self-measured memory-layer results per provider and transport, fingerprint-gated
---

# Memory Baselines

Per-provider, per-transport measurements of the neosian memory layer,
produced by the library's own harness (`kind: memory`, DESIGN §13.12).
These are **self-measured** numbers: there is no public benchmark for
the agent-memory regime (LoCoMo and its kin measure the personalization
regime — QA over long conversation histories, not agent-curated working
memory), so the shipped pack is the yardstick and this file states the
derivation rather than implying it. An external accepted-benchmark
yardstick (candidate: LongMemEval) is roadmapped as NC6.

## What is measured

The shipped pack `examples/eval_memory_baseline.yaml` — ten scenarios,
one per behavior:

| Scenario | Behavior |
|---|---|
| write-discipline | Facts filed in the right mount, one document per topic, a refused secret never stored |
| recall-next-session | A later session reads the fact back (fresh index, `view`, no rewrite) instead of guessing |
| dedup | A second fact on the same topic updates the existing document — never a near-duplicate |
| contradiction | A reversed fact is updated and the stale value survives in no live text |
| long-horizon-recall | The session-1 fact survives two unrelated writing sessions and is recalled in session 4 |
| correct-wrong-memory | A disavowed note is deleted — the claim gone from every live document |
| reflection-close | Facts stated in passing reach the store through the session close (§15 reflection): dedup-disciplined, the refused token kept out — counts and content pinned, never version histories (in-session and boundary writes both legitimate) |
| maintenance | The §16 gardener over a seeded, polluted store (`seed:`, NG): byte-dupes and empty docs fall deterministically, the misfiled user-durable fact is promoted cross-mount, the fresh document survives the deletion floor — counts, promoted content by prefix, and the pollution's absence pinned, never the promoted name |
| cross-client | **The switching claim** (VISION; §21.7, NB): a Claude Code session lands through the record verb's engine (`record:` — its hooks' three payloads, no model in the room), then the agent under test starts the way a hook-fed agent starts (`session_start:` — the index plus "where we left off" in its prefix) and must recall the foreign turn verbatim through the server's `recall_turn` (conversation required); the sessions document the record wrote and a reading session that never writes are the store truth |
| skills | **Skills as documents** (§24, NK): the agent saves a reusable procedure as `skills/<name>` through the memory tool — the frontmatter `description` the guide names is the store truth — loads it by name in the next session (`load_skill`, and loading never writes), then revises it in place: one document under `/project/skills/`, `[created, modified]`, the new step in the live text |

Scoring is **store truth** (DESIGN §13.12): after each session the
harness re-reads the actual files through a freshly constructed store.
Turn expectations stay loose; the store carries the strictness. Since
ledger #82, document *naming* is the model's: expectations pin the
mount region and the fact (`path_prefix`, exactly one matching live
document), never the file name.

## Methodology

- The external runs (`tests/external/cross/test_memory_baselines.py`)
  derive **scriptless copies of the shipped pack in code** (ledger #68)
  — one source of scenario truth. The same pack, fully scripted, is the
  keyless FakeProvider regression gate (`make test`), all-green by
  construction.
- One measured model per serving stack, the set the library's external
  tier pins (`_PROVIDER_CASES` and `LANES`): `claude-sonnet-5`,
  `gpt-5.6-sol`, `gpt-oss-120b` **and** `qwen-3.8-27b` (two rows on the
  Cerebras adapter — a row is provider+model, so neither says anything
  about the other), `gemini-3.8-flash`, `grok-4.6`. Every other shipped
  row rides the catalog probe on every dispatch, and each row carries its
  provider's lifecycle as data (`ModelSpec.retires` / `card_until`,
  DESIGN §31 — the 30-day alarm in `make test`). History: OpenAI's row
  moved `gpt-5-mini` → `gpt-5.1` on 2026-08-22 (the flagship rule) and
  `gpt-5.1` → `gpt-5.6-sol` on 2026-09-10 (NW1, ledger #210); Gemini's
  from 3.7 to 3.8 the same day; earlier tables name the model they
  measured. The 2026-08-21 tables also carry a fourth, since-removed
  provider — see the historical note under the table.
- Transports axis (ledger #64): `function` (the plain function tool),
  `cli` (the `neosian memory` engine in-process), and `http` (the same
  function tool over `RemoteStore` against an in-process state
  process — every command's store I/O crosses the twelve-route wire,
  DESIGN §18/ledger #113), and `mcp` (the memory server consumed through
  `McpServer` over the official MCP client — every tool call crosses the
  MCP wire, DESIGN §25) run for every provider; `native_memory`
  (Anthropic's `memory_20250818`) is informative only on Anthropic
  (ledger #43/#44) and runs there as a fourth cell.
- A cell passes only if every session's store truth holds. **A red run
  is the baseline doing its job** — scenarios are not tuned to make a
  provider pass, and this file records reds as found.
- Candidate lanes (NC2 slice B) are paced on our side to the account's
  requests-per-minute tier (`tests/external/pacing.py`), so their
  wall-clock is not comparable across rows; cells and behavior are.
- A door lane's board runs under an in-loop ceiling
  (`tests/external/board.py`, ledger #211): one `run_evaluation` per
  cell under a single `asyncio.timeout` of the lane's budget, a paced
  lane split into one board per transport. A cut run keeps every
  finished cell whole and records the started cell and the rest as
  `timeout` reds in the same block — recorded, never masked (dispatch
  #14's Kimi lane idled to the job cap with nothing recorded).
- Honest limits: FileStore holds the bytes in every cell (the http
  transport crosses the wire to a FileStore backing — the substrate
  itself is not varied); the model-visible tool is identical on
  `function`, `cli`, `http` and `mcp` (the mcp column's result joins
  the reminder into the text — MCP's own shape), so those columns
  separate infrastructure fidelity, not model behavior; the discriminating
  negatives that prove the scoring bites live in unit tests
  (`tests/unit/evaluation/test_memory_scripted.py`).

## Fingerprints

The current gated files; each dated results block names the pack
fingerprint it measured. A unit test (`tests/unit/test_baselines.py`)
fails when any gated file changes without this section being updated —
**no prompt-pack change without a recorded baseline re-run.**

- `neosian/assets/prompts/memory.yaml` — sha256
  `0ae69cc2d470d5fd38114220be87a4200e079afdcdf6a377929e07b13f88d9b3`
  *(NF slice B, 2026-09-05 — the `params:` map: one description per
  memory-tool parameter, now in the schema the model gets (DESIGN
  §27.9); `tool` and `system_section` unchanged byte for byte. The
  schema shape also changed in the same slice (nullable optionals as
  `anyOf`, no titles). The real rows' re-run is owed to the next /ship
  — the NM/NQ precedent. Prior: `8e2d503e…`.)*
  *(NK, 2026-09-04 — one sentence under mount routing: skills are the
  documents under `skills/` in a mount, read by `list_skills` and
  `load_skill` (DESIGN §24). Measured keylessly on the 30-cell pack and
  on every real row by dispatch #11 (run 33861838521, the dated block
  under Results). Prior: `a809ebed…`.)*
  *(NG slice B, 2026-08-22 — the run-3 no-secrets strengthening: the
  bullet now names API keys/tokens, "not even to note that one exists";
  plus one sentence introducing the index's fold lines.)*
- `neosian/assets/prompts/reflection.yaml` — sha256
  `4d91dc1b40353d6518fb1e4daab6e8306c8a47f9aa9f007aea629993f2947447`
  *(NQ on the nq-wire lane, merged 2026-09-02 — the untrusted-data fence:
  bodies and the transcript ride between per-call `<<<data …>>>` lines the
  prompt names as data, never instructions, plus the omitted-body and
  redacted rules;
  REVIEW.md MC-1/MC-3. The re-run is owed to the /ship after `nq-done`
  (NQ closed 2026-09-02 at v0.82.1 without it — the dispatch runs on
  pushed master, the NM precedent); it landed the same day as run
  33638132799, the dated block below. Prior fingerprint `f3877511…`.)*
  *(NG slice B, 2026-08-22 — the run-3 red's actual prompt path: the
  stored-token write was a reflection-boundary op, so the no-secrets
  rule leaves the closing paragraph and stands alone, transcript-secrets
  named explicitly.)*
- `neosian/assets/prompts/maintenance.yaml` — sha256
  `0612ad3efdcf8e5118c77ed06d92374c48a09045c0031f2fd29559dfbd21dad1`
  *(NQ on the nq-wire lane, merged 2026-09-02 — the untrusted-data fence:
  bodies ride between per-call `<<<data …>>>` lines the
  prompt names as data, never instructions, plus the omitted-body and
  redacted rules;
  REVIEW.md MC-1/MC-3. The re-run is owed to the /ship after `nq-done`
  (NQ closed 2026-09-02 at v0.82.1 without it — the dispatch runs on
  pushed master, the NM precedent); it landed the same day as run
  33638132799, the dated block below. Prior fingerprint `0870e367…`.)*
  *(NG slice B, 2026-08-22 — the no-secrets rule aligned to the same
  vocabulary as memory/reflection; first measured cells arrive with the
  maintenance scenario in this same batch.)*
- `examples/eval_memory_baseline.yaml` — sha256
  `70f8fa0ae776e22dbc6e69699ae080f6aefbb382a1f9cc71128a2482160831a7`
  *(NZ, 2026-09-10 — the recorded reds settled: two pins of the NV
  wordform class widened, no scenario, turn or count moved.
  `long-horizon-recall`'s `distractor-two` pinned "main" within 24
  one-line characters of "branch"; every reachable red on it (dispatches
  #5, #13, the NX pre-flight run) showed a live `/project/deployment_branch`
  the pin refused, and the settling dispatch — the first to carry the
  bytes — read its whole body: `main`. The path is the key and the body
  the value, so the pin is the value in the project region, `\bmain\b`,
  never the phrasing (the session's first widening, a stem near the
  value, was measured by that dispatch at `faca0af4…` and still refused
  the one-word body). `write-discipline`'s Postgres pin dropped its left
  word boundary and one-line 24-char window (`v16`, `Postgres16`, a
  version on the next bullet all count; a live `/project/postgres.txt`
  refused on dispatch #11). The `60 requests` literal stands: its reds
  were the fact unfiled, never a phrasing. The failure line now carries
  each live document's opening bytes and the external suite prints a red
  cell's store, so a red is read from the log, not guessed at. The
  final pin's re-run rides the next dispatch. Prior: `d3f08d55…`.)*
  *(NC1, 2026-09-04 — the transport axis gains `mcp`: the memory server
  consumed through `McpServer` over the official MCP client (DESIGN
  §25), the fourth column beside function, cli and http. No scenario,
  turn or prompt changed — the pack is 40 cells keyless (the dated
  block under Results); the real rows' `mcp` cells landed the same day
  by dispatch #12 (run 33877497375, the block under Results; Kimi's
  lane timed out). Prior: `555d584a…`.)*
  *(NK post-close, 2026-09-04 — the `skills` scenario's three turns
  drop their first-tool pins for loose response words; the store carries
  the verdict (dispatch #11's lesson, the block under Results). User
  ruling the same day: no re-dispatch for this — the rows stand as
  measured at `2bfcc8ce…`, the change is the test's shape, not a
  prompt's; the next dispatch, whenever a phase owes one, measures it.
  Prior: `2bfcc8ce…`.)*
  *(NK, 2026-09-04 — the tenth scenario, `skills` (DESIGN §24.4): a
  skill written through the memory tool with the frontmatter the guide
  names, loaded by name in the next session, revised in place; the nine
  earlier scenarios byte-unchanged. Measured keylessly, then on every
  real row by dispatch #11 the same day — run 33861838521, the block
  below; cells are N/10. Prior: `58775721…`.)*
  *(NU, 2026-09-04 — `cross-client`'s sessions document moves from
  `/user` to `/project`: the record verb now files it in the mount at
  `/project` when one is present (DESIGN §22), and the pack's mounts are
  that layout. The expectation path and the reading session's count moved
  with it; nothing a model sees changed. Dispatch #9 re-measured every
  row the same day — run 33839996089, the block below. Prior
  fingerprint `b01d2e26…`.)*
  *(NB post-close, 2026-09-03 — `cross-client` re-shaped after dispatch
  #6's Anthropic board: the reading session now asks for a ticket id
  that sits past the log line's digest and in no document, pinned by
  `response` alone — a right answer is the recall, whatever the agent
  reads first (Sonnet viewed the sessions document before recalling,
  which the first-tool pin called a miss; OpenAI and xAI recalled
  first, 3/3; Cerebras answered from the prefix's digest line in two
  columns, paraphrasing past the word — the detail is now unreachable
  without the recall). Dispatch #7 then showed Sonnet recalling and
  answering "FETCH-4821" — the key restyled with a hyphen — so the pin
  is the ticket's digits alone, the one spelling no model restyles
  (prior fingerprint `d87592ed…`; dispatch #8 landed the same day —
  run 33794983198, the block below — green on every row).
  Prior fingerprint `bdd33cdd…`, the close's shape; dispatch #7 owed.)*
  *(NB slice B, 2026-09-03 — the ninth scenario, `cross-client`: a
  hook-fed Claude Code session (`record:`) read at session start and
  recalled by the agent under test (`session_start:`, DESIGN §21.7);
  the eight earlier scenarios byte-unchanged. Measured keylessly on
  FakeProvider at the close (the dated block below); every real row's
  re-run rode the /ship after `nb-done` the same day — run 33784492062,
  dispatch #6, the dated block below — and its cells became N/9. Prior
  fingerprint `2fb60d23…`.)*
  *(NM slice B, 2026-08-31 — the transports line gains `http` (ledger
  #113): scenarios untouched, the axis widens. Every provider inherits
  the http column; the dispatched re-run (ledger #89) landed the same
  day as run 33435548947 — the dated block directly below. The prior
  fingerprint was `70dce55c…` — NG slice B's eight-scenario pack, which
  the 2026-08-22 tables measured.)*

## Candidates

A row is earned (ROADMAP §NW): a candidate door is wired
(`tests/external/lanes.py`, NC2 slice B1), the shipped pack runs
scriptless against it on dispatch, its first run lands as a dated block
below, and green over dispatched runs ships the row first-party (a
`Model` member with its sealed card, DESIGN §31); red exits, the way the
fourth provider did. Cadence is the operator's (ledger #89). B2 ruled the
five on 2026-09-02 (ledger #124); NW1 re-entered two on 2026-09-10
(ledger #211):

| Serving stack | Model | Suite | Keys | Wired | Runs | Ruled |
|---|---|---|---|---|---|---|
| xAI | grok-4.6 | `xai` | `XAI_API_KEY` | 2026-09-01 | 24, 22, 24, 23 | **shipped** — `GROK_4_6`; the 22 and the 23 are one cell, `long-horizon-recall`'s distractor, the class the shipped rows lose too |
| Google Gemini API (OpenAI-compatible endpoint) | gemini-3.7-flash | `gemini` | `GEMINI_API_KEY` | 2026-09-01 | 24, 24 | **shipped** — `GEMINI_3_7_FLASH`; two clean boards, every probe green, no provider error on the funded project |
| DeepSeek | deepseek-v4-pro | `deepseek` | `DEEPSEEK_API_KEY` | 2026-09-01 | 21, 21, 21 (structured output, every run) | **exited** — one deterministic cause, no `json_schema` on the endpoint; re-entry is a `json_object` dialect knob (§19.7) |
| Alibaba Model Studio (Singapore, token plan) | qwen3.8-max | `qwen` | `DASHSCOPE_API_KEY` | 2026-09-01 | 21, 23, 20 | **exited** — behavior reds in every run, the `forbidden` pin fired twice, the reasoning-echo class red on the round trip |
| Moonshot | kimi-k3 | `kimi` | `MOONSHOT_API_KEY` | 2026-09-01 | 24, 21 (429s, harness), 22, 23, 24 (a probe miss, the probe's own) | **stays a candidate** — run 3 was the door's own (schema misses, §19.7); run 5 a clean board with the metering probe wrong about whole-prompt cache hits; ships on the user's ruling (#124) |
| DeepSeek | deepseek-flash | `deepseek` | `DEEPSEEK_API_KEY` | 2026-09-10 | — | **candidate again** (ledger #211) — two door knobs, each a documented fact: `json_mode="json_object"` (the schema in the prompt, validation ours) and `echo_reasoning` (the documented 400 in tool loops); first board owed to NW1's dispatch |
| Alibaba Model Studio (Singapore, token plan) | qwen3.8-max | `qwen` | `DASHSCOPE_API_KEY` | 2026-09-10 | — | **candidate again** (ledger #211) — the echo knob only (`json_schema` is documented on the 3.8 series); the `forbidden` pin stays real signal; `qwen-3.8-27b` on Cerebras is a separate, measured row |

An exited row's data leaves the tree — the lane, the marker, the CI
column, the secret, its SERVICES.md row — and its measured runs stay
below as history, the way the fourth provider's did (ledger #84).

The flagship tier per stack (the 2026-08-22 ruling recorded below); ids,
limits and door knobs from the provider docs as read on the wiring date,
corrected by the door probes (`tests/external/cross/test_doors.py`) at
the first run — DESIGN §19.7 lists what those docs already predict.

## Known limits

The model classes the pack records as found, run after run, on the
shipped rows — settled at NZ (2026-09-10, ledger #208) as **the
model's**, not the harness's, and named in the v1.0.0 declaration's
evidence. Each stays measured every dispatch; none is tuned around
(per-model prompt tuning is post-v1). Each entry names its evidence.

- **The project fact filed into the user mount** (gpt-5.1 on every
  board since the calibrated pack — dispatches #5, #11, #13, the NX
  pre-flight run 33984972237, #14, #15 — and gpt-oss-120b on #14). Two
  cells, one class. `write-discipline`'s `record` session reads
  `expected 1 document(s) under /project, got 0 (live: none)` on every
  red, and #15's bytes name the destination: `/user/preferences` —
  `- Drinks: only espresso` / `- Project DB: Postgres 16`. `long-horizon-
  recall`'s `distractor-one` reads `no document under /project matching
  contains '60 requests' (live: none)`, and #15's bytes: `/user/pet.md` —
  the cat, then `2: The user's project has an API rate limit of 60
  requests per minute.` The same model files both facts correctly on
  another column of the same run. The record's earlier "wordform regex"
  and "unfiled" wordings were read off the failure line; the bytes say
  filed, in the wrong mount. A mount-routing miss, stochastic per
  column — exactly the discipline the scenario measures.
- **`skills` written without frontmatter** (Gemini on every column,
  gpt-oss and Sonnet's axis run intermittently): the guide's
  `description` key never reaches a writer that skips `list_skills` —
  ruled at NK (the store truth stays the pin).

## Results

### 2026-09-10 — The final pin measured; the reds read whole (NZ /ship, dispatch #15)

Measured by one dispatched run —
run 34503001397
(on master at 895cb31: the pack at `70f8fa0a…` — `distractor-two`
pinned to the value alone — the memory prompt unchanged at `0ae69cc2…`;
the red-cell store dump now printing document bodies, frontmatter
stripped). Every cell named from the CI log; every red's bytes in it.

| Provider | Model | function | cli | http | mcp | native | door probes | link |
|---|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | — | re-typed |
| OpenAI | gpt-5.1-2025-11-13 | 9/10 | 9/10 | 9/10 | 9/10 | n/a | — | handle |
| Cerebras | gpt-oss-120b | 10/10 | 7/10 | 9/10 | 9/10 † | n/a | — | re-typed |
| xAI | grok-4.6 | *403* | *403* | *403* | *403* | n/a | *403* | *403* |
| Gemini | gemini-3.7-flash | 9/10 | 9/10 | 9/10 | 9/10 | n/a | 6/6 | handle |
| Moonshot (candidate) | kimi-k3 | *timed out* | *timed out* | *timed out* | *timed out* | n/a | 6/6 | handle |

Findings, recorded as found:

- **The bytes name the wrong mount.** gpt-5.1's two Known-limits cells
  moved to new columns and, for the first time, said where the fact
  went: `write-discipline` on cli — `/user/preferences`: `- Drinks: only
  espresso` / `- Project DB: Postgres 16`; `long-horizon-recall`
  (`distractor-one`) on function — `/user/pet.md`: the cat, then `2:
  The user's project has an API rate limit of 60 requests per minute.`
  Filed, in the user mount: one class, not two (the entry above and
  ledger #208 corrected the same day). Its other two reds are `skills`
  on http and mcp, and this time the frontmatter was there — the `use`
  session rewrote the skill on load (`[created, modified]` where loading
  must not write), a second face of the same class.
- **The final pin held — and the class it answers has a second face.**
  gpt-oss's `long-horizon-recall` is green on function, http and mcp
  with `deployment_branch` bodies of the one word `main` (the cli cell
  fell to the first-tool pin: `load_skill` before the memory write, the
  store truth intact — `cat.md: 'Cat name: Biscuit'`, `api_rate_limit`,
  `deployment_branch: 'main'`); the harness's verdict of dispatch #14
  stands measured. The same key-value shape then showed on
  `write-discipline` (cli): `/user/drink_preference: 'espresso'`,
  `/project/postgres_version: '16'` — the path the key, the body the
  bare value — and the Postgres pin (`postgres(ql)?…16`) refused it as
  the old `main…branch` pin refused `main`. Recorded, not changed: the
  pin to the value alone, or a prefix match that reads path and body
  together (a §13.4 change), is a ruling, not a /ship fix. Its cli
  `correct-wrong-memory` fell to the same first-tool pin (`load_skill`
  first). **The first fully green gpt-oss function column.**
- **† Cerebras's hourly cap ended the lane:** the last cell (mcp ×
  `skills`) and the gpt-oss catalog probe failed with 429 "Requests per
  hour limit exceeded" at 17:24 UTC, the harness level, not a cell —
  the mcp column reads 9/10 on the nine cells that ran. Two dispatches
  in one day plus the 40-cell pack is past the account's tier; the
  pacer (`tests/external/pacing.py`) clocks the door lanes only.
  `gemma-4-31b` answered `model_not_found` again (the two adapter tests
  and its probe): the row is dead, dispatch #14's finding repeated.
- **Sonnet 40/40 and the axis 20/20** — the first fully green Anthropic
  lane since the ten-scenario pack; the link re-typed, as every run.
- **Gemini 36/40:** every red `skills` without frontmatter (the bodies
  now in the log: `# Release Skill` headings, no `description:` key),
  probes 6/6, the handle.
- **xAI did not measure** — the 403 of dispatch #14, unchanged.
- **Kimi's row did not measure, a fourth time** — probes 6/6, the
  handle, then the baseline over the 3600 s timeout at the paced tier.
  This time the timeout ended the test (`Failed: Timeout (>3600.0s)`,
  the lane 1:04 h) where dispatch #14's had only dumped stacks and
  idled two hours to the job cap — so the interruption is not reliable
  in either direction. The candidate's membership, timeout or pack
  size, and the lane's own ceiling, stay the user's ruling.

### 2026-09-10 — The recorded reds settled from the bytes (NZ, dispatch #14)

Measured by one dispatched run —
run 34457119814
(on `phase/nz-reds` at a7811ad: the memory prompt unchanged at
`0ae69cc2…`, the pack at `faca0af4…` — the session's first widening of
the two pins, see Fingerprints; the failure line and the external suite
now carrying the bytes). Every cell named from the CI log. The NX
pre-flight run 33984972237 (2026-09-05, at the publication) went
unrecorded here; its reds were the classes below and its logs are part
of the evidence.

| Provider | Model | function | cli | http | mcp | native | door probes | link |
|---|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/10 | 10/10 | 10/10 | 10/10 | 10/10 | — | re-typed |
| OpenAI | gpt-5.1-2025-11-13 | 9/10 | 9/10 | 9/10 | 9/10 | n/a | — | handle |
| Cerebras | gpt-oss-120b | 9/10 | 7/10 | 9/10 | 8/10 | n/a | — | re-typed |
| xAI | grok-4.6 | *403* | *403* | *403* | *403* | n/a | *403* | *403* |
| Gemini | gemini-3.7-flash | 9/10 | 10/10 | 9/10 | 9/10 | n/a | 6/6 | handle |
| Moonshot (candidate) | kimi-k3 | *timed out* | *timed out* | *timed out* | *timed out* | n/a | 6/6 | handle |

Findings, recorded as found — the first board whose reds are read from
the bytes:

- **The three cells NZ works last, settled.** OpenAI's
  `write-discipline` (cli): the store dump holds one document,
  `/user/preferences`, and nothing under `/project` — the model's, the
  Known limits entry above; function, http and mcp green this run.
  OpenAI's `long-horizon-recall`: green on all four columns — the
  distractor class is stochastic, recorded as the model's on its
  history. Cerebras's `long-horizon-recall` `distractor-two` (mcp):
  `(live: … /project/deployment_branch: 'main')` — the body is the one
  word, the path the key: the harness's, and the first widening (a
  deploy/branch/release stem near the value) still refused it, so the
  pin became the value alone (`70f8fa0a…`, Fingerprints), its re-run
  owed to the next dispatch.
- **gpt-oss files the project fact elsewhere too:** `write-discipline`
  on cli, the store holding `/user/drink_preference` only — the same
  class as gpt-5.1's, now on two rows. Its cli `long-horizon-recall`
  fell to the first-tool pin (`list_skills` before the memory write on
  `distractor-two`; both distractor documents live), the #11
  collateral class.
- **`skills` without frontmatter is the largest class on the board:**
  OpenAI on function/http/mcp, Cerebras on all four, Gemini on
  function/mcp (its http cell wrote on load instead — `[created,
  modified]` where loading must not write), Sonnet's axis run on
  function (it asked a clarifying question before saving — the turn's
  response pin, the store untouched). The NK ruling stands.
- **Sonnet 39/40 + the axis 19/20:** function `correct-wrong-memory` —
  the disavowed note survived as a second version of `/project/notes.md`
  (the softened edit, dispatch #12's reason); native 10/10.
- **xAI did not measure:** every call 403 — the team's credits or
  monthly limit spent, the state the NX pre-flight run hit; an account
  act, not a cell.
- **Cerebras stopped serving `gemma-4-31b`:** twelve `model_not_found`
  404s on the row's catalog probe and the two adapter tests that name
  it; the same probe passed on both 2026-09-05 runs. The shipped enum
  row is dead on its serving stack — an NW membership fact for the
  user's ruling (removal is a public-surface change), recorded not
  decided.
- **Kimi's row did not measure, a third time:** door probes 6/6 and
  the link by handle, then the 40-cell baseline crossed the 3600 s
  per-test timeout at the account's paced tier — and this time the
  timeout only dumped stacks: the paced async loop was never
  interrupted, the runner idled to the job's 180-minute cap and GitHub
  cancelled it (no API spend in those two hours, one runner-hour
  each). Two facts for the user's ruling: the candidate's membership,
  timeout or pack size (#12, #13), and a per-test timeout that cannot
  end an async test — the lane's own ceiling is the harness's to fix,
  recorded not decided.

### 2026-09-05 — The freeze list's schemas on every row (NF, dispatch #13)

Measured by one dispatched run —
run 33948369389
(at bffa9bf / v0.90.0: the memory prompt at fingerprint `0ae69cc2…` —
the `params` map, one description per memory-tool parameter, DESIGN
§27.9 — the pack unchanged at `d3f08d55…`; the tool schemas themselves
reshaped by NF slice B: nullable optionals as `anyOf`, `$defs`, every
parameter described, no titles, every call validated before the body).
Every cell named from the CI log.

| Provider | Model | function | cli | http | mcp | native | door probes | link |
|---|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 | — | re-typed |
| OpenAI | gpt-5.1-2025-11-13 | 10/10 | 9/10 | 9/10 | 9/10 | n/a | — | handle |
| Cerebras | gpt-oss-120b | 9/10 | 8/10 | 9/10 | 7/10 | n/a | — | handle |
| xAI | grok-4.6 | 10/10 | 10/10 | 9/10 | 10/10 | n/a | 7/7 | handle |
| Gemini | gemini-3.7-flash | 9/10 | 9/10 | 9/10 | 9/10 | n/a | 6/6 | handle |
| Moonshot (candidate) | kimi-k3 | *timed out* | *timed out* | *timed out* | *timed out* | n/a | 6/6 | handle |

**The new schema crossed every wire as a schema:** on every row that
ran, every call bound and validated — no cell is red for a schema,
validation or transport reason, and the door probes, catalog and link
cells passed on every lane. Sonnet's four-column provider baseline is
**40/40 for the first time**; its Anthropic axis run (function +
`native_memory`, a second measurement of the same model) went 9/10 on
function — `skills` written without frontmatter — and 9/10 on native
(`correct-wrong-memory`: the disavowed fact stayed under `/project`,
the #12 reason). Two measurements of one column disagreeing by one
cell is the run-to-run variance every dispatch has shown.

**The reds are the familiar ones, moved:** gpt-5.1's `write-discipline`
on cli/http/mcp (the `record` session's Postgres fact never reached
`/project`; function green), gpt-oss's `long-horizon-recall` on
function/cli/http and its mcp column at 7/10 (`write-discipline`,
`correct-wrong-memory`, `skills`), grok's http `long-horizon-recall`.
**`skills` without frontmatter** stays the one recurring reason:
Gemini red on all four columns this time (its mcp cell was green in
#12), gpt-oss on cli and mcp, Sonnet's axis function run — the ruling
stands (per-model tuning post-v1). The link column: Sonnet re-typed
the URL this run, every other row passed the handle.

**Kimi's row did not measure, again:** door probes 6/6 and the link
cell green (the handle), then the 40-cell baseline exceeded the 3600 s
per-test timeout at the account's paced tier (the lane ran 1:04 h) —
#12's finding repeated; the candidate's membership, timeout or pack
size remains the user's ruling, recorded not decided.

### 2026-09-04 — The mcp column on every row (NC1, dispatch #12)

Measured by one dispatched run —
run 33877497375
(at 3be3080 / v0.88.0: the pack at fingerprint `d3f08d55…` — the
`mcp` transport, DESIGN §25.5 — the corrected `skills` scenario's
first real measurement, and the prompts unchanged since `8e2d503e…`).
Every cell named from the CI log; the `mcp` column is the memory
server consumed through `McpServer` over the official client.

| Provider | Model | function | cli | http | mcp | native | door probes | link |
|---|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/10 | 10/10 | 10/10 | 9/10 | 10/10 | — | handle |
| OpenAI | gpt-5.1-2025-11-13 | 9/10 | 10/10 | 10/10 | 10/10 | n/a | — | handle |
| Cerebras | gpt-oss-120b | 9/10 | 10/10 | 10/10 | 10/10 | n/a | — | re-typed |
| xAI | grok-4.6 | 10/10 | 9/10 | 10/10 | 9/10 | n/a | 7/7 | handle |
| Gemini | gemini-3.7-flash | 9/10 | 9/10 | 9/10 | 10/10 | n/a | 6/6 | handle |
| Moonshot (candidate) | kimi-k3 | *timed out* | *timed out* | *timed out* | *timed out* | n/a | 6/6 | re-typed |

**The mcp column measures as the door should:** on every row that ran
it is green or within one cell of the function column — Sonnet's
`correct-wrong-memory` (red on function *and* mcp: the disavowed fact
stayed under `/project`), grok-4.6's `long-horizon-recall` — and no
cell failed for a transport reason: every `is_error` crossed as an
in-band failure, the skills pair and `recall_turn` served by the
server itself landed the `skills` and `cross-client` scenarios.

**`skills`, first measured in its corrected shape:** green on Sonnet
(all five columns — dispatch #11's first-tool pin was the test's, as
ruled), on gpt-5.1 (four columns; #11's "loading writes" did not
recur), on gpt-oss-120b (four columns; #11's missing frontmatter did
not recur), on grok and on Gemini's mcp column. **Gemini's other three
columns stay red on `skills`** (no frontmatter, the #11 reason) — the
one row where the recorded model behaviour held. The rows that
flipped green did so with no prompt change, so #11's per-row reasons
are one-run observations, not stable traits; the ruling stands
(per-model tuning post-v1).

**The other reds moved, as they do run to run:** Sonnet's
`correct-wrong-memory` (function, mcp — cli last time), gpt-5.1's
`write-discipline` (function only — all three columns last time),
gpt-oss's `long-horizon-recall` (function — http last time), grok's
cli `contradiction` and mcp `long-horizon-recall` (its #11 first-tool
collateral on `recall-next-session` is green). The link column moved
too — Sonnet passed the handle this time, kimi re-typed — one run
each way.

**Kimi's row did not measure:** the door probes, catalog and link
cells ran (about five minutes), then the 40-cell baseline exceeded the
external tier's 3600 s per-test timeout at the account's paced tier
(the lane ran 1:05 h in total; #11's 30 cells fit). The fourth column
is 25 % more cells on a lane that was already the slowest; whether the
candidate gets a longer timeout, a smaller pack, or exits is the
user's membership ruling — recorded here, not decided.

### 2026-09-04 — MCP client-side: the fourth column, keyless (NC1)

The pack gained the `mcp` transport (DESIGN §25.5) — the memory server
consumed through `McpServer` over the official MCP client, every tool
call crossing the MCP wire the way a third-party agent consumes the
daemon's `/mcp`; the pack fingerprint moved (the section above), no
scenario, turn or prompt did. The keyless row, at the close: **40/40**
on FakeProvider across function, cli, http and mcp
(`tests/unit/evaluation/test_memory_scripted.py`) — every scenario
green on the new column, `skills` and `cross-client` included (the
server serves the skill tools and `recall_turn` itself, so the mcp
column carries nothing through `extra_tools` but the bridge). The real
rows followed the same day — dispatch #12, the block above.

### 2026-09-04 — The ten-scenario pack on every row; the link column (NK, dispatch #11)

Measured by one dispatched run —
run 33861838521
(at 12bf5d1 / v0.87.0: the pack at fingerprint `2bfcc8ce…`, the
memory prompt at `8e2d503e…` — the tenth scenario `skills` and the one
mount-routing sentence, DESIGN §24.4). Every cell named from the CI
log; the `link` column is `tests/external/cross/test_link_handles.py`'s
`handle_used` — the cell owed by dispatch #10 (the test passed on every
row: the tool received the URL; the column says whether the model
passed the `[link N]` handle or re-typed the URL).

| Provider | Model | function | cli | http | native | door probes | link |
|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/10 | 8/10 | 9/10 | 9/10 | — | re-typed |
| OpenAI | gpt-5.1-2025-11-13 | 8/10 | 7/10 | 8/10 | n/a | — | handle |
| Cerebras | gpt-oss-120b | 8/10 | 9/10 | 8/10 | n/a | — | re-typed |
| xAI | grok-4.6 | 8/10 | 9/10 | 9/10 | n/a | 7/7 | handle |
| Gemini | gemini-3.7-flash | 9/10 | 9/10 | 9/10 | n/a | 6/6 | handle |
| Moonshot (candidate) | kimi-k3 | 8/10 | 9/10 | 9/10 | n/a | 6/6 | handle |

**The nine earlier scenarios hold** at their known levels: Sonnet's
cli `correct-wrong-memory`, gpt-5.1's `write-discipline` (the Postgres
regex, all three columns) and cli `long-horizon-recall`, gpt-oss's
function `write-discipline` and http `long-horizon-recall`, Kimi's
`cross-client` reading session writing on the function column — each
seen on earlier dispatches. One new collateral: **grok-4.6's function
`recall-next-session`** failed the first-tool pin because the model
called `list_skills` before writing the memory (the store truth passed).

**`skills` is red on every row, for three distinct reasons** — the
first two are the scenario's and the prompt's, not the models':

- **The first-tool pin trips on discovery** (Sonnet on all four
  columns, grok-4.6, kimi-k3): the model calls `list_skills` first —
  exactly what the tool description invites — *then* writes the skill
  with the frontmatter `description`, under `/project/skills/`, as the
  contract asks. Store truth passed; `expect: {tool: memory}` failed.
  The cross-client note already named this hazard of the matcher's
  first-tool rule.
- **No frontmatter** (gemini-3.7-flash on all three columns, gpt-oss-120b
  on all three): both wrote `/project/skills/release` as a plain
  markdown body (`# Release Skill …`). Neither called `list_skills`
  first, so neither saw the writing guide, which today reaches a model
  only as that tool's reminder; the prefix sentence names `skills/`
  but not the `description` key. A real gap in where the contract
  lives, not in the models.
- **Loading writes** (gpt-5.1 on all three columns): asked to *use*
  the skill, the model loaded it and then revised it twice
  (`created, modified, modified`) — "loading never writes" is a
  genuine miss on this row, the same shape as Kimi's reading session
  on `cross-client`.

**Ruled the same day (user):** the second and third reasons are model
behaviour — one prompt cannot fit every model, and per-model prompt
tuning is post-v1 work, measured here row by row; the prompts stay as
they are. The first reason is the test's, and is fixed now: the skills
scenario's turns keep only a loose response word and the store carries
the verdict (fingerprint `555d584a…`, above). No re-dispatch for that
fix — hours of real-API time for a test-shape change — so these rows
stand as measured at `2bfcc8ce…`; the next owed dispatch measures the
corrected scenario, where Sonnet, grok and kimi's `skills` cells are
expected green and Gemini's, gpt-oss's and gpt-5.1's stay red for the
recorded reasons.

### 2026-09-04 — Skills: the tenth scenario, keyless (NK)

The pack gained `skills` (DESIGN §24.4) and `memory.yaml` one sentence;
both fingerprints moved (the section above). The keyless row, at the
close: **30/30** on FakeProvider across function, cli and http
(`tests/unit/evaluation/test_memory_scripted.py`), `skills` green on all
three columns — the cli column carrying the two skill tools through
`extra_tools`, the http column loading the skill over the wire. The
real rows followed the same day — dispatch #11, the block above.

### 2026-09-04 — Link handles: the keyless measurement (NJ)

Not a memory-pack row: NJ's `[link N]` handles (DESIGN §23) are
measured on the compacted view, keylessly, in
`tests/unit/conversation/test_links.py::TestMeasured` — the same
eight-turn link-heavy history projected with whole tokens and with
handles, estimated by the window's own `ContextPolicy`:

| History | Estimated tokens (whole) | Estimated tokens (handles) | Saved |
|---|---|---|---|
| 8 turns, one 70-char URL in every USER and AGENT segment | 483 | 223 | 53 % |

The real question — does a model reuse a link *by its handle* after a
boundary — is `tests/external/cross/test_link_handles.py`: one cell per
provider row (the three adapters and every lane), the tool's receipt
asserted (the URL it was handed), the handle's use printed. Its first
dispatched run is dispatch #10, the block below.

### 2026-09-04 — Link handles on every row; the pack at v0.86.0 (NJ, dispatch #10)

Measured by one dispatched run —
run 33847958205
(at 00bb73d / v0.86.0: the log line atom-safe and contracted through
`[link N]` handles — a model-visible change to the compacted view,
outside the fingerprinted pack, which no memory scenario crosses).
Every cell named from the board; the run reads red on two lanes, each
carrying a class already recorded.

| Row | Model | function | cli | http | native_memory | Probes | link reused after a boundary |
|---|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/9 | 9/9 | 9/9 | 9/9 | — | ✓ |
| OpenAI | gpt-5.1-2025-11-13 | 8/9 | 9/9 | 8/9 | n/a | — | ✓ |
| Cerebras | gpt-oss-120b | 9/9 | 9/9 | 9/9 | n/a | — | ✓ |
| xAI | grok-4.6 | 9/9 | 9/9 | 9/9 | n/a | 7/7 | ✓ |
| Gemini | gemini-3.7-flash | 9/9 | 9/9 | 9/9 | n/a | 6/6 | ✓ |
| Moonshot (candidate) | kimi-k3 | 8/9 | 9/9 | 9/9 | n/a | 6/6 | ✓ |

Findings, recorded as found:

- **Every row reused the link after the boundary.** Six of six cells of
  `test_link_handles.py` green: with the URL's turn compacted to a line
  carrying `[link 1]`, each model opened the document and the tool was
  handed the original URL — the recall claim, asserted. *Whether* the
  model passed the handle or recalled the turn first was printed, and
  pytest captures stdout on a passing test, so this run does not say;
  the external step gains `-s` in the commit that records this block,
  and dispatch #11 transcribes the `handle_used` column here.
- **The handles changed nothing the pack measures.** Four whole boards
  (Anthropic with the native axis, Cerebras, xAI, Gemini) on the first
  run with contracted log lines; xAI's distractor class of dispatch #9
  did not recur.
- **OpenAI's known class stands:** `write-discipline` red on function
  and http (the Postgres fact unfiled under `/project`), cli green —
  still the cell NZ's "recorded reds" session works last.
- **Kimi's reading session wrote, again, on one column.** `cross-client`
  red on function only (cli and http green this run): the same
  unasked-for `fetch-retry-ticket` document beside the sessions
  document. The candidate stays a candidate (NW: green over two
  consecutive runs ships).

### 2026-09-04 — The home, and the sessions document at /project (NU, dispatch #9)

Measured by one dispatched run —
run 33839996089
(at 56dcea0 / v0.85.0: the `cross-client` sessions document expected at
`/project`, the mount the record verb now files it in; fingerprint
`58775721…`). Every cell named from the board; the run reads red on
three lanes, each carrying a class named below.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/9 | 9/9 | 9/9 | 9/9 | — |
| OpenAI | gpt-5.1-2025-11-13 | 8/9 | 9/9 | 8/9 | n/a | — |
| Cerebras | gpt-oss-120b | 9/9 | 9/9 | 9/9 | n/a | — |
| xAI | grok-4.6 | 9/9 | 8/9 | 9/9 | n/a | 7/7 |
| Gemini | gemini-3.7-flash | 9/9 | 9/9 | 9/9 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 8/9 | 8/9 | 9/9 | n/a | 6/6 |

Findings, recorded as found:

- **The moved document changed nothing a model sees.** Every row's
  `cross-client` reads the sessions document from `/project` and
  recalls the foreign turn as before — 17 of 19 cells green, the two
  reds below Kimi's own.
- **Cerebras is a whole board for the first time** — 27/27 across the
  three columns; its two recorded classes (`write-discipline` on
  function, `long-horizon-recall` on http) did not recur this run.
- **OpenAI's known class stands:** `write-discipline` red on function
  and http (the Postgres fact unfiled under `/project`), cli green —
  the cell NZ's "recorded reds" session works last.
- **xAI meets the distractor class:** `long-horizon-recall` on cli —
  the "60 requests" fact unfiled — the class OpenAI and Cerebras carried
  on earlier runs, on xAI for the first time; function and http green.
- **Kimi's reading session wrote.** `cross-client` red on function and
  cli: after recalling the foreign turn, `kimi-k3` filed a
  `fetch-retry-ticket` document under `/project` — two documents where
  the pin allows one. A right answer with an unasked-for write; the
  "reading never writes" pin is the harness's store-truth rule, so the
  cell is red and Kimi stays a candidate (NW: green over two runs
  ships; this lane has now read red on its own schema misses and on
  this write).

### 2026-09-03 — The switching claim green on every row (NB post-close, dispatch #8)

Measured by one dispatched run —
run 33794983198
(at 100eca8: the `cross-client` pin is the ticket's digits; fingerprint
`b01d2e26…`, the pack as it stands). Every cell named from the board;
the run reads red because two lanes carry the known classes.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 9/9 | 9/9 | 9/9 | 9/9 | — |
| OpenAI | gpt-5.1-2025-11-13 | 8/9 | 8/9 | 8/9 | n/a | — |
| Cerebras | gpt-oss-120b | 8/9 | 9/9 | 8/9 | n/a | — |
| xAI | grok-4.6 | 9/9 | 9/9 | 9/9 | n/a | 7/7 |
| Gemini | gemini-3.7-flash | 9/9 | 9/9 | 9/9 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 9/9 | 9/9 | 9/9 | n/a | 6/6 |

Findings, recorded as found:

- **`cross-client` is green in all 19 cells** — six rows, every column
  they run, native_memory included: a Claude Code session written
  through the record verb's engine, read at session start, recalled
  verbatim through `recall_turn` by a different model, the ticket only
  the verbatim turn holds answered every time. The switching claim
  VISION cites is measured, not asserted, on every shipped row and the
  candidate.
- **Anthropic and xAI are whole boards:** 36/36 and 27/27 with 7/7
  probes.
- **The reds are the known classes:** OpenAI's `write-discipline` on
  all three columns this run (the misfiled Postgres fact), Cerebras'
  `write-discipline` on function and `long-horizon-recall` on http.
  None touch NB's surface.
- **Three dispatches in one day** (#6, #7, #8) to get the scenario's
  pin right: a first-tool pin, then a substring a model restyled. The
  lesson stands in the fingerprint notes — pin what only the measured
  behavior can produce, spelled the one way no model rewrites.

### 2026-09-03 — The re-shaped switching claim (NB post-close, dispatch #7)

Measured by one dispatched run —
run 33789728169
(at 2fdb2d8: `cross-client` re-shaped after dispatch #6 — the reading
session asks for a ticket id that only the verbatim turn holds, pinned
by `response` alone; fingerprint `d87592ed…`). Every cell named from
the board; the run reads red because four lanes carry a red cell.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/9 | 9/9 | 9/9 | 9/9 | — |
| OpenAI | gpt-5.1-2025-11-13 | 7/9 | 9/9 | 9/9 | n/a | — |
| Cerebras | gpt-oss-120b | 9/9 | 9/9 | 8/9 | n/a | — |
| xAI | grok-4.6 | 9/9 | 8/9 | 9/9 | n/a | 7/7 |
| Gemini | gemini-3.7-flash | 9/9 | 9/9 | 9/9 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 9/9 | 9/9 | 9/9 | n/a | 6/6 |

Findings, recorded as found:

- **The switching claim holds on every row: 17 of 18 cross-client
  cells green, and the eighteenth recalled too.** With the detail
  unreachable without the recall, every model on every column called
  `recall_turn(cc-7f3a, 1)` and answered from it — Cerebras included,
  which had answered from the digest line in dispatch #6. Sonnet's one
  red on function is the answer's spelling: it recalled and wrote
  "FETCH-4821", restyling the key with a hyphen, and the substring pin
  read `FETCH4821`. The pin is now the ticket's digits alone (the
  fingerprint note above); dispatch #8 re-measures that cell.
- **The other reds are the known classes, moving between columns as
  they do:** OpenAI's `write-discipline` and `long-horizon-recall` on
  function, Cerebras' `write-discipline` on http, xAI's
  `long-horizon-recall` on cli (`distractor-one`).

### 2026-09-03 — The nine-scenario pack on every row (NB, dispatch #6)

Measured by one dispatched run —
run 33784492062
(at f15b9cc: NB closed at v0.84.0 — the pack fingerprinted as
`bdd33cdd…`, the close's shape of `cross-client`). Every cell named from
the board; the run reads red because four lanes carry a red cell each.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/9 | 9/9 | 9/9 | 8/9 | — |
| OpenAI | gpt-5.1-2025-11-13 | 8/9 | 9/9 | 9/9 | n/a | — |
| Cerebras | gpt-oss-120b | 8/9 | 8/9 | 8/9 | n/a | — |
| xAI | grok-4.6 | 9/9 | 9/9 | 8/9 | n/a | 7/7 |
| Gemini | gemini-3.7-flash | 9/9 | 9/9 | 9/9 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 9/9 | 9/9 | 9/9 | n/a | 6/6 |

Findings, recorded as found:

- **The switching claim measured on real models, first pass.** Gemini,
  Kimi, xAI and OpenAI recalled the foreign turn through `recall_turn`
  with the right `conversation` and `turn` on every column they ran —
  15 of 15 cells — and answered from it.
- **Anthropic: 8/9 twice, and the miss is the pin's, not the model's.**
  Sonnet `view`ed the sessions document the index lists, *then* called
  `recall_turn(cc-7f3a, 1)` and answered correctly with "backoff"; the
  scenario's `tool:` expectation is a first-tool pin, so a sensible read
  before the recall counted as a miss. Re-shaped the same day (the
  fingerprint note above): the asked-for detail now sits past the log
  line's digest and in no document, pinned by `response` alone — a right
  answer is the recall. Dispatch #7 re-measures.
- **Cerebras: 8/9 on every column** — `cross-client` on function and
  cli (it answered from the prefix's digest line, paraphrasing past the
  word; on cli it read memory first), `write-discipline` on http (its
  known class). The re-shape reaches the first two: without the recall
  the ticket cannot be answered.
- **OpenAI 8/9 (write-discipline on function) and xAI 8/9
  (long-horizon-recall on http, `distractor-one`)** — the classes their
  records already show; unchanged by NB.
- **Kimi's probe stands at 6/6** after dispatch #5's cache-read fix;
  membership stays the user's ruling (#124).

### 2026-09-03 — The cross-client scenario, measured keylessly (NB slice B)

The ninth scenario's first measurement is the keyless gate itself:
`make test` runs the shipped pack on FakeProvider across the three
shipped transports (`tests/unit/evaluation/test_memory_scripted.py`),
and the switching claim's negatives — the wrong turn recalled, a
reading session that writes — are red by construction. On the `http`
column the recall crosses the state process's wire through
`RemoteStore`; the record itself always lands through the verb's
engine on the cell's backing FileStore, as a hook would.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| FakeProvider (the keyless gate) | fake | 9/9 | 9/9 | 9/9 | n/a | — |

The real rows' 9/9 re-measurement — a real model reading a foreign
session and recalling it — landed the same day as dispatch #6, the
block above.

### 2026-09-02 — NQ's packs measured; the tier's ceiling fitted (dispatch #5)

Measured by one dispatched run —
run 33638132799
(at 193d0e1: NQ closed at v0.82.1 — the fenced, budgeted reflection and
maintenance packs fingerprinted above, their first measurement — plus the
external tier's hour-per-test ceiling). The dispatch fired first at
b79c77e (run 33636956805)
killed every lane's baseline at 60 s: the unit tier's `pytest-timeout`
ceiling (TP-12, slice A) had never been fitted to a benchmark that runs
minutes; 193d0e1 marks every external item with an hour, inside the
job's 180. Every cell named from the board.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 | — |
| OpenAI | gpt-5.1-2025-11-13 | 7/8 | 7/8 | 7/8 | n/a | — |
| Cerebras | gpt-oss-120b | 7/8 | 8/8 | 8/8 | n/a | — |
| xAI | grok-4.6 | 8/8 | 8/8 | 8/8 | n/a | 7/7 |
| Gemini | gemini-3.7-flash | 8/8 | 8/8 | 8/8 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 8/8 | 8/8 | 8/8 | n/a | 5/6 |

Findings, recorded as found:

- **The fenced packs cost the clean rows nothing.** Anthropic 32/32 in
  10 minutes (a seventh), xAI 24/24 with every probe green in 8, Gemini
  24/24 with every probe green in 29 — the data fence and the latched
  budget changed no verdict on a row that was clean before.
- **OpenAI: 21/24** — `long-horizon-recall` on function (`distractor-one`,
  the "60 requests" fact never filed under `/project`) and
  `write-discipline` on cli and http (the wordform regex): the two
  classes every prior gpt-5.1 board shows; four reds last run, three now.
- **Cerebras: 23/24** — `long-horizon-recall` on function at
  `distractor-two` (the main/branch regex), the distractor class the
  shipped rows lose; three clean boards before it.
- **Kimi: 24/24 on the pack in 48 minutes, zero 429s; the one red is the
  probe's own.** `system_prompt_is_honored` got its PONG and then
  asserted `input_tokens > 0` while Moonshot served the whole 106-token
  prompt from cache (input 0, cache_read 106) — the wire's accounting is
  right (cache reads are metered apart from input, ECOSYSTEM §4), the
  probe was wrong about whole-prompt cache hits and now asserts the
  prompt was metered either way. Record 24, 21, 22, 23, 24; membership
  stays the user's ruling (#124).

### 2026-09-02 — Gemini's second run; the rulings' record (NC2 B2, dispatch #4)

Measured by one dispatched run —
run 33609542397
(at f934f6e, fired for Gemini's second lane; the pack fingerprinted
above, unchanged). Every cell named from the board; the last run of the
five-candidate matrix — B2 ruled on this record (the Candidates table).

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 | — |
| OpenAI | gpt-5.1-2025-11-13 | 8/8 | 6/8 | 6/8 | n/a | — |
| Cerebras | gpt-oss-120b | 8/8 | 8/8 | 8/8 | n/a | — |
| xAI (candidate) | grok-4.6 | 8/8 | 7/8 | 8/8 | n/a | 7/7 |
| Gemini (candidate) | gemini-3.7-flash | 8/8 | 8/8 | 8/8 | n/a | 6/6 |
| DeepSeek (candidate) | deepseek-v4-pro | 7/8 | 7/8 | 7/8 | n/a | 6/7 |
| Model Studio, token plan (candidate) | qwen3.8-max | 8/8 | 7/8 | 8/8 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 7/8 | 8/8 | 8/8 | n/a | 6/6 |

Findings, recorded as found:

- **Gemini: 24/24 again, every probe green, 29 minutes paced** — two
  consecutive clean boards; the row ships (`GEMINI_3_7_FLASH`).
- **xAI: 23/24** — `long-horizon-recall` at `distractor-one` on cli, the
  same cell as run 2; its record 24, 22, 24, 23, probes 7/7 ×4.
- **Kimi: 23/24, zero 429s in 53 minutes** — `maintenance` on function,
  the misfiled fact unpromoted (the model stage); the fourth run, still
  not a clean board since run 1.
- **Qwen: 23/24** — `maintenance` on cli; the round-trip probe passed
  again (the reasoning-echo class is intermittent). **DeepSeek: 21/24**
  — the same three maintenance cells, the same `json_schema` refusal.
- **OpenAI: 20/24** — `write-discipline` on cli (the wordform regex),
  `long-horizon-recall` on cli and http, `maintenance` on http.
- **Cerebras: 24/24 on the pack**, a third clean board; one adapter
  test in its own suite hit a 429 (`queue_exceeded`, high traffic) —
  the suite, not the baseline. **Anthropic 32/32**, a sixth.

### 2026-09-02 — the third run; Gemini's first (NC2 slice B, dispatch #3)

Measured by one dispatched run —
run 33603887909
(at a181a9b: the pacer a tenth wider with six retries, `GEMINI_API_KEY`
set; the pack fingerprinted above, unchanged). The first run with all
five candidate lanes armed; every cell named from the board.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 | — |
| OpenAI | gpt-5.1-2025-11-13 | 7/8 | 7/8 | 8/8 | n/a | — |
| Cerebras | gpt-oss-120b | 8/8 | 8/8 | 8/8 | n/a | — |
| xAI (candidate) | grok-4.6 | 8/8 | 8/8 | 8/8 | n/a | 7/7 |
| Gemini (candidate) | gemini-3.7-flash | 8/8 | 8/8 | 8/8 | n/a | 6/6 |
| DeepSeek (candidate) | deepseek-v4-pro | 7/8 | 7/8 | 7/8 | n/a | 6/7 |
| Model Studio, token plan (candidate) | qwen3.8-max | 8/8 | 5/8 | 7/8 | n/a | 5/6 |
| Moonshot (candidate) | kimi-k3 | 7/8 | 8/8 | 7/8 | n/a | 6/6 |

Findings, recorded as found:

- **Gemini: 24/24 on its first run, every probe green, no provider
  error in 28 minutes paced at 5 requests/min.** The thought-signature
  echo (`ToolCall.extra`) held across every memory tool loop, and the
  `stop`-finish fix carried the streamed-tool-call probe. The daily cap
  never appeared on the funded project.
- **Kimi: 22/24 with zero 429s** — the widened pacer holds. Both reds
  are structured calls whose JSON failed schema validation: one
  reflection at the boundary (`reflection-close` on function wrote
  nothing), one maintenance model stage (`maintenance` on http did not
  promote). The structured-output probe passes every run, so the door
  honors the schema nominally and misses it intermittently — a
  door-quality finding for B2, not a memory-behavior miss.
- **Qwen: 20/24, and the tool round trip went red for the first time.**
  Three cli cells (a second write on `/user/preferences`, the disavowed
  MongoDB note kept, the promotion missed) and one http cell. In the
  probe the model re-issued the tool call after receiving its result;
  its response carried reasoning the OpenAI wire drops when it echoes
  the assistant turn — the reasoning-echo class §19.7 predicted for
  Kimi, measured on Qwen. Model Studio also emits `index` on completion
  tool calls, echoed back harmlessly (the same echo passed in run 2).
- **xAI: 24/24** (its record now 24, 22, 24); **Cerebras 24/24** for the
  second consecutive run; **Anthropic 32/32** for the fifth.
- **OpenAI: 22/24** — `write-discipline` on function (the wordform
  regex) and `long-horizon-recall` on cli. **DeepSeek: 21/24** — the
  same three maintenance cells, 21 refusals of `json_schema`.

### 2026-09-02 — the candidate doors' second run (NC2 slice B, dispatch #2)

Measured by one dispatched run —
run 33595428001
(at f4a36b7: the per-door pacer, the board print; the pack fingerprinted
above, unchanged). Gemini's lane ran vacuously again (its secret landed
after the dispatch); every cell below is named from the log's board.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 | — |
| OpenAI | gpt-5.1-2025-11-13 | 7/8 | 7/8 | 7/8 | n/a | — |
| Cerebras | gpt-oss-120b | 8/8 | 8/8 | 8/8 | n/a | — |
| xAI (candidate) | grok-4.6 | 8/8 | 7/8 | 7/8 | n/a | 7/7 |
| DeepSeek (candidate) | deepseek-v4-pro | 7/8 | 7/8 | 7/8 | n/a | 6/7 |
| Model Studio, token plan (candidate) | qwen3.8-max | 8/8 | 8/8 | 7/8 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 8/8 | 6/8 | 7/8 | n/a | 6/6 |

Findings, recorded as found:

- **Cerebras: 24/24** — its first clean board in six runs; the
  distractor-two cell that was red in every earlier run passed.
- **xAI: 22/24 after 24/24.** Both reds are `long-horizon-recall` at
  `distractor-one` (cli and http): the "60 requests" fact never filed
  under `/project` — the memory-worthiness class, the same scenario that
  costs the shipped rows their cells. Every probe green again.
- **Qwen: 23/24 after 21/24.** One red, `maintenance` on http — the
  misfiled fact not promoted. The three behavior reds of run 1 did not
  recur. All probes green; the token plan held a full lane again.
- **Kimi: 21/24 after 24/24, all three reds are 429s.** Two cells died
  as harness errors (`reflection-close` on cli, `correct-wrong-memory`
  on http): Moonshot answered "The engine is currently overloaded" and
  five rate-limit refusals that outlasted the pacer's three retries;
  the third (`maintenance` on cli) degraded its model stage on the same
  class. Not model behavior. The pacer now spaces requests a tenth
  wider than the tier and retries a 429 six times with growing waits.
- **DeepSeek: 21/24 again, the same three cells** — `maintenance` on
  every transport, the model stage degraded by the endpoint's
  `json_schema` refusal (21 such 400s; reflection degraded six times).
  Deterministic; the row's fate is B2's ruling.
- **OpenAI: 21/24** — `write-discipline` red on all three transports,
  the postgres/16 wordform regex; the maintenance promotion it missed
  in run 1 landed this time.
- **Anthropic: 32/32** on four transports, a fourth consecutive clean
  board.

### 2026-09-01 — the candidate doors' first run (NC2 slice B, dispatch #1)

The first dispatch with the candidate lanes armed — four of the five
(Gemini's secret waits on AI Studio prepaid credits; its lane ran
vacuously). Measured by one dispatched run —
run 33556146015
(at 8601d39: the pacing layer, the token-plan Qwen door and
`ToolCall.extra` in the tree; the pack fingerprinted above, unchanged).
The three shipped rows re-measured in the same run. Cells are scenarios
passed per transport; a candidate's seven door probes stand beside them.

| Row | Model | function | cli | http | native_memory | Probes |
|---|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 | — |
| OpenAI | gpt-5.1-2025-11-13 | 19/24 across the three, see the note | | | n/a | — |
| Cerebras | gpt-oss-120b | 7/8 | 8/8 | 8/8 | n/a | — |
| xAI (candidate) | grok-4.6 | 8/8 | 8/8 | 8/8 | n/a | 7/7 |
| DeepSeek (candidate) | deepseek-v4-pro | 7/8 | 7/8 | 7/8 | n/a | 6/7 |
| Model Studio, token plan (candidate) | qwen3.8-max | 6/8 | 8/8 | 7/8 | n/a | 6/6 |
| Moonshot (candidate) | kimi-k3 | 8/8 | 8/8 | 8/8 | n/a | 5/7 (harness) |

Findings, recorded as found:

- **xAI: 24/24 and every probe green** — the first candidate row to
  clear a full board; membership needs one more consecutive run.
- **DeepSeek is blocked by structured output, not behavior.** Its
  endpoint refuses the `json_schema` response format ("This
  response_format type is unavailable now" — 19 such 400s in the lane),
  so every reflection call degraded (six; those cells still passed on
  in-session writes) and every maintenance model stage degraded
  (three), leaving the misfiled fact unpromoted on all three transports
  — the same three cells red for one systematic reason. §19.7's "the
  pack never needs it" was wrong: `reflect:` and `maintain:` need it.
  The door's fate — exit, or a `json_object` dialect with the schema in
  the prompt — is B2's ruling.
- **Qwen (token plan): 21/24, three behavior reds.** `reflection-close`
  on function wrote nothing to `/user` at the boundary (no document
  carrying the espresso fact); `maintenance` on function promoted the
  timezone fact but the model stage also deleted the surviving `coffee`
  document — over-pruning past the byte-safe stage; `correct-wrong-memory`
  on http kept the disavowed MongoDB note, the `forbidden` pin's second
  real fire. All probes green; the token plan's fixed host held for a
  full lane (~11 min).
- **OpenAI: 19/24 — five red cells, two named.** `maintenance` on
  function did not promote the misfiled fact (a judgment miss; the same
  model promoted it on 2026-08-22 and 2026-08-31) and `write-discipline`
  on cli hit the postgres/16 wordform regex (the NV class). The other
  three are unnamed: pytest truncates the assertion's repr and the run
  predates the board print in `_assert_baseline` — the next dispatch
  carries every cell.
- **Cerebras: 23/24.** `long-horizon-recall` on function
  (`distractor-two`) missed the main/branch pin — this cell's fifth
  stochastic red across five runs and three providers; still the §13.13
  content-matcher judge's case (NC6). No re-roll, per the standing rule.
- **Kimi: 24/24, paced at 3 requests per minute for 47 minutes.** The
  second candidate to clear a full board. Its two red probes and the
  catalog smoke were the harness's fault, not the door's: each pytest
  test built its own pacer, so consecutive tests restarted the clock
  and burst past the organisation's tier (nine 429s), and a stream had
  no 429 retry. Fixed the same day — one clock per door for the whole
  process, a stream retries a 429 before its first chunk, the catalog
  smoke rides the lane's clock — so run #2 measures the door alone.
- **The wire and the pacing are behavior-neutral.** Anthropic ran the
  full board green on four transports again, and every one of Kimi's
  24 pack cells passed under pacing.

### 2026-08-31 — the http column (NM slice B: the wire under the models)

The pack's transports line gained `http` (ledger #113 — the memory tool
over `RemoteStore` against an in-process state process; the fingerprint
above): scenarios untouched, the axis widened to 24 cells per provider.
The keyless gate stands at 24/24. Measured by one dispatched run —
run 33435548947
(at 1116335, the pack fingerprinted above). Cells are scenarios passed
per transport.

| Provider | Model | function | cli | http | native_memory |
|---|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 | 8/8 |
| OpenAI | gpt-5.1-2025-11-13 | 8/8 | 8/8 | 7/8 | n/a |
| Cerebras | gpt-oss-120b | 8/8 | 6/8 | 8/8 | n/a |

Findings, recorded as found:

- **The wire is behavior-neutral, as designed.** Anthropic ran the
  full board green — 8/8 on all four transports — and every http red
  elsewhere is a known model-behavior class, not a wire failure: in
  each red cell the turn expectations passed and the captured memory
  calls executed `ok` over the wire; what failed was store truth about
  what the model chose to write.
- **gpt-5.1: 23/24.** The one miss (`http`, `write-discipline`) is the
  stochastic memory-worthiness class: zero `/project` documents — the
  model answered about PostgreSQL 16 but never filed the fact (the
  same session's function and cli cells filed it). The transport is
  model-invisible, so the variant it landed on is chance.
- **gpt-oss-120b: 22/24, both reds on cli.** `write-discipline` filed
  `/project/postgres_version` but in words outside the tolerant
  postgres/16 regex — the NV wordform class (live doc named; the bytes
  are unrecoverable post-hoc on CI runners). `long-horizon-recall`
  (`distractor-two`) missed the main/branch pin again — this cell's
  **fourth** stochastic red across four runs and three providers,
  still the §13.13 content-matcher judge's case (NC6). No re-roll,
  per the standing rule.

### 2026-08-22 — the calibrated pack; OpenAI's flagship row

The privacy-note ruling landed (reflection-close's count pins dropped —
the fingerprint annotations above) and OpenAI's row moved to
`gpt-5.1-2025-11-13`. Measured by one dispatched run —
run 32563464178
(at 97c8ad7, the pack fingerprinted above). Cells are scenarios passed
per transport.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 |
| OpenAI | gpt-5.1-2025-11-13 | 8/8 | 7/8 | n/a |
| Cerebras | gpt-oss-120b | 7/8 | 8/8 | n/a |

Findings, recorded as found:

- **The calibration held.** `reflection-close` is green everywhere it
  ran — including gpt-5.1 on both transports — with the refused token
  stored nowhere; the privacy-note class no longer reads as a dedup
  miss, and the maintenance scenario stayed green on all six cells.
- **gpt-5.1's first row: 15/16.** The flagship's one miss is the same
  cell Cerebras missed (below), not the reflection class that moved
  the row here.
- **The branch-fact cell misfired on two providers** —
  `long-horizon-recall`, session `distractor-two`: zero live `/project`
  documents matched the order-tolerant main/branch regex on OpenAI
  (cli) and Cerebras (function). The turn expectation passed (a
  `memory` call was made), so the fact was either not filed or filed in
  words outside even the tolerant pin — this cell's third stochastic
  red across three runs and three providers (run 2 Cerebras word-order,
  run 3 OpenAI unfiled, now both). Recorded as found, no re-roll;
  the stored bytes cannot be inspected post-hoc — CI runners discard
  the store roots, an honest limit of dispatched runs versus local
  ones. This recurring class is exactly what the §13.13
  content-matcher judge (NC6) is for.

### 2026-08-22 — eight-scenario pack (NG slice B: the gardener measured)

One batched fingerprint change (the four annotations above): the run-3
no-secrets strengthening across all three prompt assets, the index
fold-lines sentence, and the seeded `maintenance` scenario —
maintenance.yaml's first measured cells. The keyless gate stands at
16/16 (eight scenarios × function/cli). Measured by one dispatched run
the same day —
run 32559735287
(at 2854e03, the pack fingerprinted above); the 2026-08-21 table below
measured the seven-scenario pack and stands as history. Cells are
scenarios passed per transport.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 8/8 | 8/8 | 8/8 |
| OpenAI | gpt-5-mini-2025-08-07 | 7/8 | 8/8 | n/a |
| Cerebras | gpt-oss-120b | 8/8 | 8/8 | n/a |

Findings, recorded as found:

- **The maintenance scenario's first cells are green on all six.**
  Every provider ran the gardener over the seeded polluted store and
  emitted the cross-mount promotion; the deterministic dedup/prune and
  the fresh document's survival held everywhere — the §16 engine
  measured on real models on its first outing.
- **The run-3 Cerebras stored-token red did not recur** under the
  strengthened prompts (one run — a data point, never a trend claim).
- **OpenAI `reflection-close` (function): a second `/user` document,
  `privacy_preference`.** The reflection pass recorded the user's
  privacy instruction as its own document — without the token
  (`forbidden` held). This is the class the 2026-08-21 ruling already
  named memory-worthy when it fired on write-discipline (that `/user`
  count was dropped); `reflection-close` still pinned
  `counts: {/user: 1}`, so the same legitimate behavior read as a
  dedup miss there. **Ruled legitimate (user, 2026-08-22):** the
  write-discipline ruling extends to the reflection boundary —
  reflection-close's count pins dropped in both sessions, dedup kept
  by exactly-one-match (a duplicated drink document is two matches),
  the no-secrets rule untouched; pack re-fingerprinted above. The
  same ruling moved OpenAI's row to the flagship model — see
  Methodology.

### 2026-08-21 — seven-scenario pack (NR: reflection joins)

The NR phase added the `reflection-close` scenario, the
`reflection.yaml` prompt asset (both fingerprinted above), and the
harness's `reflect:` session key. Measured by three dispatched runs the
same day —
run 32518636050
(at 56d6f80),
run 32520488461
(at 3e9cda9, after the strict-schema fix below), and
run 32524270383
(at 684cb59, after the two ruled calibrations — the pack fingerprinted
above; runs 1–2 measured its pre-calibration twin). Cells are scenarios
passed per transport, one cell per run.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 7/7 · 7/7 · 7/7 | 7/7 · 7/7 · 7/7 | 7/7 · 7/7 · 7/7 |
| OpenAI | gpt-5-mini-2025-08-07 | 6/7 · 6/7 · 6/7 | 7/7 · 7/7 · 7/7 | n/a |
| Cerebras | gpt-oss-120b | 7/7 · 6/7 · 7/7 | 7/7 · 6/7 · 6/7 | n/a |

Three-run read: Anthropic is stable at 21/21 cells; OpenAI and
Cerebras hover one stochastic cell short, and after the run-1/2
calibrations landed, run 3's misses are **genuine behavior findings**
(below), not pin artifacts — the trained-behavior asymmetry the
roadmap's risk section names, now measured per scenario.

Findings, recorded as found (screened for calibration, deliberately
unruled — tuning a scenario to make a provider pass is never silent):

- **ReflectionBatch strict-mode 400 (run 1, fixed).** OpenAI's strict
  structured output requires every property in `required`; the flat
  all-optional op schema 400'd every reflection call there (the
  `reflection-close` cells still passed — the model legitimately wrote
  in-session instead). Fixed at 3e9cda9: per-command all-required op
  variants in a plain `anyOf` union; a keyless wire-schema pin guards
  the class. Run 2 shows the engine clean on OpenAI.
- **OpenAI `write-discipline` (red both runs, function).** gpt-5-mini
  files the preference correctly and never stores the token — but also
  writes a second `/user` document recording the privacy instruction
  itself (`privacy` / `secrets`), failing `counts: {/user: 1}`.
  **Ruled legitimate (user, 2026-08-21):** a privacy-preference note is
  memory-worthy — the `/user` count dropped (the exactly-one-match
  espresso rule and the `forbidden` pin keep the bite); pack
  re-fingerprinted above.
- **Cerebras `long-horizon-recall` (green run 1, red run 2, both
  transports).** The model filed "Deployment branch: main" while the
  pack's regex `(?i)\bmain\b.{0,6}branch` pins the "main … branch"
  word order — the NV wordform class again, stochastic across runs.
  **Ruled (user, 2026-08-21):** order-tolerant regex (either word
  order, both words near); pack re-fingerprinted above. Third widening
  of the phrasing class — the same ruling adds the **content-matcher
  judge** (external tier only, §13.13's reserved shape) to the NC6/NG
  options-first agenda: deterministic fact-presence pins over model
  prose are the harness's weak layer; structural checks (counts,
  forbidden, versions, exactly-one-match) stay deterministic
  everywhere.
- **Run 3 (post-calibration): the calibrated pins held** — neither
  phrasing red recurred — **and the two remaining reds are the pack's
  first genuine behavior catches**, recorded as found and *never*
  calibration candidates:
  - **Cerebras `reflection-close` (cli): the refused token stored.**
    gpt-oss-120b wrote `/user/security` containing the literal
    `sk-eval-secret-000` the user said never to save — the `forbidden`
    pin's first real catch (the count red rode along). The no-secrets
    pin is not weakenable; this is a model-behavior finding for the
    prompt-pack/NG lane and the standing Cerebras row.
  - **OpenAI `long-horizon-recall` (function): the fact never filed.**
    Only `/project/api-rate-limit` was live — the branch fact from
    distractor-two was not recorded at all this run (the same model
    passed this scenario on runs 1–2). A stochastic
    memory-worthiness/filing miss, not a phrasing artifact.
  - Chasing an all-green board by re-dispatching until it lands would
    be selection bias; the three-run table stands as the baseline.

### 2026-08-21 — six-scenario pack (local runs)

Cells are scenarios passed per transport (store truth, all sessions).
Source: per-provider local runs of
`tests/external/cross/test_memory_baselines.py` against the
fingerprinted pack above. `n/a` = the native transport degrades by
construction off Anthropic (ledger #44), so only Anthropic runs it.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 6/6 | 6/6 | 6/6 |
| OpenAI | gpt-5-mini-2025-08-07 | 6/6 | 6/6 | n/a |
| Groq ‡ | openai/gpt-oss-120b | 3/6 † | 3/6 † | n/a |
| Cerebras | gpt-oss-120b | 6/6 | 6/6 | n/a |

† Groq's failures are **harness-level, not store-level**: gpt-oss-120b
failed to emit a valid `memory` tool call ("failed to generate valid
tool call after 2 retries") in roughly half the cells, with the failing
cells shifting between runs — a stochastic emission failure, observed
consistently across three runs (the CI dispatch and two local runs).
Every cell that completed passed store truth. Notably, **Cerebras runs
the same model weights and passed 12/12** (its free tier throttles —
the run took ~14 minutes) — the emission failure is an inference-stack
difference, not a weights difference. This is the trained-behavior
asymmetry the roadmap's risk section names, measured.

‡ Since removed — the membership rule's first application (NW step 0,
ledger #84); the row stands as dated history.

### Run-informed calibrations (2026-08-21)

The first real runs exposed pins the models were never told about; each
was released deliberately (the fingerprint gate records the pack they
produced), and the communicated disciplines all stayed pinned — the
unit-tier negatives prove the scoring still bites:

- **Document naming** → `path_prefix` expectations (ledger #82).
- **Provenance annotations**: "Lives in Lisbon (moved from Paris)" is a
  good update — the `forbidden:` pins on superseded values dropped.
- **Filing granularity**: one consolidated project doc vs two separate
  docs are both disciplined — distractor pins became content presence,
  not document counts.
- **Wordform/typography**: "Peanut allergy" for *peanuts*,
  "PostgreSQL 16", "PostgreSQL version: 16", "the \`main\` branch" —
  literal `contains` pins became stems or word-tolerant regexes (the
  last widening after the first CI dispatch of the final pack caught
  a phrasing the local runs happened not to produce).
- **Memory-worthiness**: Sonnet declined to store "I'm planning to
  switch to MongoDB" (a musing); the record turn now asks for the note
  explicitly — the scenario measures the correction, not worthiness.

Observation, no change: on the native transport the model first tries
its trained `/memories/…` path root, hits the corrective failure, and
recovers in-turn (the library warns exactly this when no mount is named
`memories`).

### CI pipeline

- First-ever dispatched external run: 2026-08-21, run
  run 32496965995
  — three-scenario pack at v0.71.0, exact-path expectations. All four
  providers red, dominantly on exact-path pins the models were never
  told about; the finding became ledger #82 (`path_prefix`). Two real
  signals underneath: the fourth provider's emission failures above,
  and Anthropic's native transport first
  tried its trained `/memories/…` path prefix, hit the corrective
  failure, and recovered in-turn.
- Cadence: **dispatch-only** since 2026-08-21 (ledger #89 — the weekly
  schedule that stood from NV to NR was removed by user ruling; a
  standing real-API bill is not automated). Re-runs are deliberate
  `workflow_dispatch` acts, owed at every pack or prompt change (the
  fingerprint gate above enforces the recording); repo-secret keys, an
  absent secret self-skips.
