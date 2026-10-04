# Frontier model audit — 2026-10-04

The 1.4.0 implementation refreshes Anthropic and OpenAI. Provider facts below
were checked against official documentation on this date. Catalog changes
still require neosian qualification; an upstream release alone does not move
an installed package's selectors.

## Implemented catalog and wire changes

| Provider | Latest tiers | Implementation |
|---|---|---|
| Anthropic | Sonnet 5.5, Opus 5.5, Fable 5.1 | Add Sonnet 5.5; all three use summarized adaptive thinking and the binding-controls beta, with prefix mismatches dropping the bound thinking block. Merge the compaction beta when requested. Refuse forced tool choice on Sonnet 5.5. |
| OpenAI | GPT-6.1 Sol, GPT-6 Astra, GPT-6 Luna | Add Sol 6.1, including its $0.10/MTok cache-read rate. Preserve the stateless Responses path and encrypted reasoning replay. |

Sources: [Sonnet migration](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide),
[Opus changes](https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5),
[Claude effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[OpenAI model selection](https://developers.openai.com/api/docs/guides/latest-model),
[Sol 6.1](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

All six tiers accept low, medium, high, xhigh and max. Only Luna among these
current tiers accepts explicit `none`; Python `None` omits the effort field
and leaves the provider default intact. Unsupported new levels fail before
network I/O, including in a fallback ladder. Existing MAX downgrades remain.

Per-tier default/latest selectors and provider shortcuts resolve to exact
IDs in a release-owned map. Sonnet 5, GPT-6 Sol and GPT-5.1 are superseded
and warn on selection. They remain for 30 days from neosian publication;
default promotion and exact-ID removal occur in the first release after
each successor's independent window. No runtime date or network lookup
changes routing. Saved history is not rewritten.

The resident `neosian chat` agent chooses latest Sonnet unless explicitly
overridden by a flag, saved configuration or `/model`. This is the current
chat decision; the library's overall default remains Cerebras.

The price card remains the standard-context, standard-service estimate.
For Sol 6.1, the documented premium above 272K input tokens (2× input/cache,
1.5× output) is not represented by the existing flat card; consumers needing
billing accuracy must account for it separately. This refresh does not add
a tiered billing contract.

## Qualification and publication

The final keyless suite passed 4,436 tests with 95.61% coverage. Strict mypy
checked 647 source files; lint and the file-size gate passed. Wheel/sdist
builds succeeded; the wheel contains the new modules, the SDK dependency
floor and a byte-identical `llms.txt`. Importing with warnings treated as
errors succeeds; migration notices are emitted on selection, not import.

Local live qualification used configured credentials without displaying
their values. OpenAI's catalog and frontier suite passed 21 tests (22
inapplicable cases skipped); Anthropic's four catalog probes passed and
its final frontier suite passed all 18 tests. These cover effort acceptance,
complete/stream continuation, and Claude signed-thinking replay after a
changed system prefix. The first Claude test draft incorrectly required
thinking on trivial arithmetic; ten failures reflected that assumption.
The final suite tests effort acceptance separately from a harder replay
case, which requires an actual signed block.

Real SDK mock-transport tests cover complete/stream paths and combined
thinking-binding/compaction headers. Memory baseline results are recorded
in [the shipped baseline page](../neosian/assets/docs/baselines.md): the
main boards passed 46/48 cells for Sonnet and 41/48 for Sol, with extra
writes and handoff pickup failures retained as behavioral failures.
Sonnet's separate function/native-memory comparison passed 22/24 cells;
its failures were an extra skill revision and missed handoff pickup.
Local runs do not replace the dispatched release evidence.

Before publishing 1.4.0:

1. Review the measured memory results and complete the normal external
   dispatch on the release commit.
2. Stamp the actual UTC publication date in `RELEASE_DATES` in
   `neosian/_foundation/shared/model_lifecycle.py` and in the changelog's
   1.4.0 heading. The pending date is intentional, not a guessed deadline.
3. Run the default gates and `make release v=1.4.0`. Its model gate rejects
   missing dates, early removals, stale predecessors and early/late default
   promotion. Every later successor keeps its own publication date.

## Other supported models — final audit

These are findings and follow-up work, not additional migrations in 1.4.0.

| Serving stack and shipped model | Finding | Follow-up |
|---|---|---|
| xAI: `grok-4.6` | **Grok 4.7 is available.** Its published card is $2 input / $6 output per MTok, with 500K context and low/medium/high/xhigh effort. Responses returns encrypted reasoning for replay. [Official model guide](https://docs.x.ai/developers/grok-4-7). | Qualify `grok-4.7` on the existing Responses door, verify replay/effort/pricing and run the memory board before a catalog transition. Review cache routing support. The Fast variant is not available on the public API. |
| Cerebras: `gpt-oss-120b`, `qwen-3.8-27b` | Both remain the models listed for Shared Inference. Context limits differ between free and paid tiers. [Official catalog](https://inference-docs.cerebras.ai/models/overview). | No successor found in the serving catalog. Recheck account-tier limits during the next live qualification; dedicated offerings are separate. |
| Google: `gemini-3.8-flash`, `gemini-3.7-flash` | 3.8 Flash remains the current row. Its introductory $0.75/$3.75 input/output card ends 2026-12-31; the published 2027 card is $1.50/$7.50 (cache read $0.15). [Latest model](https://ai.google.dev/gemini-api/docs/generate-content/latest-model), [pricing](https://ai.google.dev/gemini-api/docs/pricing). | Keep the existing `card_until` alarm; update the rate card before 2027-01-01. Enroll 3.7 retirement in a future announced catalog transition, without backdating the window. Review compatibility-endpoint behavior on the next qualification. |
| Moonshot: `kimi-k3` | K3 remains the listed flagship. Published rates are $3 input, $15 output, $0.30 cache hit and $3 cache write per MTok. [Official catalog and rates](https://platform.kimi.ai/). | No successor found. A future metadata update should declare the verified $3 cache-write rate explicitly; the current unspecified-rate fallback already charges the same amount. |
| Alibaba Model Studio (Singapore): `qwen3.8-max` | A September snapshot, `qwen3.8-max-0902` / `qwen3.8-max-2026-09-02`, is documented. [Release notes](https://www.alibabacloud.com/help/en/model-studio/newly-released-models). Qwen3.8-Max cache discounts explicitly differ from the generic ratios, with details in the console. [Cache documentation](https://www.alibabacloud.com/help/en/model-studio/context-cache). | Verify the rolling alias's relationship to the snapshot and qualify the Singapore ID before changing membership. Keep cache prices unspecified until the exact applicable rate is verified. |
| Registered local doors and `fake` | Local model choices belong to the caller; FakeProvider remains deterministic and keyless. | No global replacement. Registered capabilities can opt into the new effort levels. |
