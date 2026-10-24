---
title: "Shared knowledge and review policy"
summary: Attribute ownership, review procedures and decisions, and select effective knowledge through explicit policies.
---

# Shared knowledge and review policy

Personal, project and organizational knowledge use explicit scopes and mounts.
Scope types are names, without membership, inheritance or automatic sharing.
A host maps its authenticated users to server grants. Owner and reviewer labels
describe responsibility; they cannot grant access. Server-stamped actors retain
the authenticated client alongside the submitted attribution.

## Review a procedure or decision

Optional `attributes.neosian_governance` in the readable knowledge header holds
format 1, kind (`knowledge`, `procedure` or `decision`), 1..16 distinct owner
labels, state (`draft`, `approved` or `superseded`), and optional UTC
`effective_at`, `expires_at` and `superseded_by: {scope, path}`. A successor is
allowed only for superseded knowledge. Dates use
`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`; expiry must be after the effective instant.
Labels have 1..256 characters without controls; whitespace-only labels refuse.
These fields are versioned and erased with the document body.

Start with an existing ordinary document. Save a governance change as `review.json`:

```json
{
  "format": 1,
  "kind": "procedure",
  "owners": ["Release team"],
  "state": "approved",
  "reviewer": "human:reviewer",
  "effective_at": "2026-10-24T00:00:00Z",
  "expires_at": "2027-10-24T00:00:00Z"
}
```

```sh
neosian knowledge govern /memories/procedures/release --scope org:demo --file review.json --expect-version 1 --reason 'Reviewed release procedure' > proposal.json
neosian upkeep apply proposal.json --scope org:demo
# Review proposal.json and the preview, then pause all writers before applying.
neosian upkeep apply proposal.json --scope org:demo --write --approve APPROVED_PROPOSAL_ID
neosian knowledge read /memories/procedures/release --scope org:demo --policy approved
```

`govern` only prepares a normal upkeep proposal. It preserves existing sources
and adds the observed revision as evidence. The proposal includes the proposed
content and full current-state guards; apply checks current mounted/server
permissions, versions, erasure and collection rules. Draft/superseded changes
omit `reviewer`; approved changes require it. Input is strict JSON, bounded to
64 KiB, and the resulting proposal has upkeep's 256 KiB limit.
Read `neosian docs memory` for interrupted runs, partial receipts, inspection
after lost responses and fresh proposals. Recovery does not bypass review.

Approved records contain `review: {by, at, sha256}`. The timestamp is preparation
time. The digest binds the destination scope/path, body, collection, attributes,
references and sources. Changing any of these makes an old approval stale;
upkeep corrections/restorations and imports that add provenance require review
again. Rename makes the old path-bound approval stale too. Envelope versions,
actors and timestamps are independent audit data, excluded from this digest.
Byte-identical content at the same identity retains the same approval assertion.

For independent writers the digest recipe is SHA-256 of compact UTF-8 JSON
`[1, scope, path, metadata, body]`, recursively sorting object keys and preserving
array order. `metadata` is the parsed knowledge header with its default empty
arrays/maps present. Remove only `attributes.neosian_governance.review` before
hashing. There is no trailing newline. The stored schema is available through
`neosian schema governance`; dates, path grammar and hashes also receive semantic
checks. No document text executes during preparation or reading.

An approval hash proves which content the assertion describes, not who approved
it. Any authorized content writer can create a review assertion. A host needing
trusted approval must control write/apply credentials and its approval workflow.
The user's agent and the host own reasoning, scheduling, membership and SSO.

## Retrieval policies

| Policy | Eligible current content |
|---|---|
| `all` | All authorized current content, including drafts and invalid review metadata |
| `effective` | Unmanaged documents, plus approved content whose hash and time window are current |
| `approved` | Only approved content whose hash and time window are current |

`knowledge read` and MCP `knowledge` default to `effective`. The response reports
`status`, `policy`, `included`, evaluation time, version and provenance. Excluded
documents have no `content` field. Status is `unmanaged`, `draft`, `superseded`,
`stale`, `scheduled`, `expired`, `approved` or `invalid`. Missing/erased documents
refuse. Effective time is inclusive; expiry is exclusive. A future review time
is also scheduled. Invalid/future metadata never qualifies for filtered reads.

`memory search --knowledge-policy approved` and MCP `search_memory` select the
same behavior. Unspecified search policy is `all`, preserving existing behavior.
The optional wire-6 `knowledge_policy: {version: 1}` capability enables the
`knowledge_policy` request field on `memory/search`. Filter before matching,
excerpts and pagination, at one server UTC instant per scan. Cursors bind policy;
every page sees current state. Older servers explicitly refuse filtered search
in the client. Search keeps no separate index or historical approval cache.

An optional `knowledge_policy = "approved"` on a profile mount sets a minimum
for searches, knowledge reads/references, startup index/full context, and selected
skill roots, dependencies and supporting documents. `all < effective < approved`;
a request may tighten that floor. An excluded full-context document is reported
unavailable, and excluded skill graphs refuse loading. Built-in immutable skills
are separate binary assets. Filtered context refuses listings over 10000 entries.
The default omitted profile policy is `all`; older strict readers reject the
new field. Importing a profile cannot broaden server grants or enable capture.
Transcript and mailbox context retain their separate policies and can contain
previously recorded copies of knowledge; review filtering does not rewrite them.

Policies select useful context; scoped grants authorize access. Raw `memory view`,
version/history reads, editing and exports remain available to their authorized
callers for inspection and recovery. They are not review-filtered. A profile
policy does not hide drafts from someone with underlying read credentials.
Keep private knowledge in separate granted scopes if readers must not see it.

`knowledge references --policy approved` uses the stricter source/target mount
policy and current target authority. Ineligible, denied or erased targets are
unavailable without a version or mounted reference. Existing references remain
path based. `superseded_by` is an additional explicit scoped reference, with no
automatic traversal, replacement, cascade or permission grant. Use an authorized
raw inspection path to inspect superseded records when a profile excludes them.
CLI references defaults to `all`; the MCP knowledge tool defaults to `effective`.

## Retention, erasure and revocation

Expiry stops filtered retrieval; it does not delete data or schedule cleanup.
Ordinary deletion retains revisions. Existing explicit operator redaction clears
current and historical content, including governance and provenance in the body,
while retaining the documented audit skeleton. Search reads authoritative current
rows; it cannot retain an erased indexed body. Retention/prune remains the existing
conversation operator workflow. It does not prune memory revisions or delete
procedures based on age.

Reviewers can recover retained revisions using upkeep; changed provenance makes
the previous approval stale. Erased content cannot be restored by that workflow.
Previously exported bundles, proposal files, copies, derived documents and host
logs require their own retention/erasure. Source erasure does not cascade into
them. Full archives retain their separate restoration contract.

The server checks grants on each request; configuration/token revocation takes
effect after server restart. Long-lived local MCP bridges reauthorize each storage
call. Already delivered context remains in the host's possession. Direct file and
Postgres connections remain operator access under OS/database permissions.

Governance revisions emit ordinary document changes when the backend supports
the durable feed. Those references are raw authorized audit data and are not
filtered by effective/approved retrieval policies. A host may use them to
reconsider cached context or request a review; neosian runs no automatic review.
See `neosian docs changes` for current grants and erasure/reference limits.
