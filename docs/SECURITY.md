# Security policy

neosian handles bearer tokens and can expose a network process (`neosian
serve`), so security reports are read first and answered privately.

## Reporting a vulnerability

Use the repository's private vulnerability reporting (the Security
tab → "Report a vulnerability"), or email **community@neosian.com**.
Never open a public issue for a vulnerability. You will get an
acknowledgement within 72 hours, a
severity assessment and a fix or a mitigation plan after that, and
credit in the release notes if you want it. Please include the version
(`neosian version`), the transport involved (CLI, MCP or HTTP), and a reproduction.

## Supported versions

The newest 2.x release is the supported line and receives fixes. Version 2
is the Rust memory/context core, without a dedicated SDK or Python product.
The retired 1.x product line, earlier tags and release candidates are not
maintained. Existing data and protocol compatibility is documented in
`neosian docs migration`.

The neosian.com service publishes its own policy and addresses; a
report that concerns both is welcome here.

## Workspace authority and capture

Portable profiles select context; they cannot grant server permission.
Profile HTTP bindings require the scoped authorization capability, with
exact memory-scope and conversation grants checked before storage access.
Grant-enabled servers refuse hosted `/mcp`; clients use local stdio bridges.
File ownership and direct Postgres credentials remain operator authority.
Legacy token prefixes remain literal prefixes and are not exact-scope grants.

Memory search checks scope read authority before scanning or constructing
excerpts and pages. Cursors do not grant access, and every resumed page is
reauthorized. Redacted/deleted documents, historical bodies and mailbox/session
records are excluded. Only explicitly selected metadata string values are
searched. Reference backends retain no search cache or derived index; results
already delivered to a client cannot be recalled after erasure.

New profiles capture off by default. Local/shared capture requires consent in
both the profile and its machine-local binding. Deterministic filters precede
the private spool, outbound turns and session summaries; they are not universal
secret detection and do not filter explicit memory writes or host-owned logs.
Policy changes retain pending spans rather than replay them into a changed
destination. Import/export excludes bindings and credentials, but includes
ordinary profile text and filter literals. See `neosian docs profiles` and
`neosian docs wire` for limits, operator privileges and recovery behavior.

## Knowledge ingestion

The knowledge workflow accepts explicit UTF-8 Markdown or format-1 bundles,
rejects symlink/traversal inputs and protects lifecycle and erased targets.
Source labels, metadata and references are untrusted data, never authority.
Reference resolution cannot read outside currently mounted permissions.
Collection schemas are advisory checks, not access control. Apply/export
requires paused writers because wire 6 has no batch transaction or atomic
compare-and-create. Bundles retain current source evidence; exported copies
require their own erasure and are not automatically synchronized.

Managed skill files and dependency declarations are data, not executable
installation hooks or permission grants. Loading checks mounted reads, profile
selection, current revision pins and supporting hashes. Raw authorized memory
reads remain available. Import/edit/revert/export require paused writers and
stable files; partial batches report receipts without rollback. Root erasure
does not cascade to separate resources or existing exports.

## Reviewed upkeep

Upkeep request/proposal JSON and stored instructions cannot grant permission.
An approval digest binds reviewed content, not a human identity or signed
authorization. Hosts control write-tool exposure and approval policy; current
mount and server permissions remain authoritative. Proposals bind the workspace,
logical sources and full observed snapshots, including erasure/metadata that
may not advance a version. They do not authenticate a physical endpoint.
Reserved lifecycle records and erased content are excluded.

Apply/recovery requires paused writers. Wire 6 has no conditional delete,
erasure-generation fence or atomic batch; a change after the final read cannot
be ruled out. Partial receipts contain acknowledged writes, and a lost response
may hide a committed write. Inspect current state and prepare fresh remaining
work. Derived content and proposal copies need separate erasure; source
erasure cannot recall a body already copied elsewhere.

## Shared knowledge review

Owner/reviewer labels and review hashes are content assertions, not authenticated
roles or signatures. Hosts requiring trusted approval must restrict write/apply
credentials and implement their approval workflow. Scope grants, token allowances
and mounted access remain authoritative; review metadata cannot change them.

Optional profile review policies filter searches, knowledge reads/references,
injected memory and selected skill graphs. They are context selection, not a
secrecy boundary: raw authorized memory/history/export paths remain available
for review and recovery. Use distinct granted scopes for private drafts.
Filtered search requires an advertised capability and excludes stale, invalid,
superseded, expired and draft records before matching or pagination. Erasure
clears review metadata with the body; expiry alone never deletes data. Prior
copies and conversation records retain their separate erasure requirements.

## Durable change references

Change pages check current scope read grants before storage; retention, including
preview, requires operator and scope write permission. A saved cursor does not
retain revoked access. Events contain paths and versions, not content, and are
raw authorized audit inspection even when a profile filters governed context.
Use separate scopes for sensitive draft names. References survive content erasure;
copies in mailboxes, exports and host state require separate handling.

A mailbox context checks a mounted current document/version when sent. It does
not grant recipients access, fetch content automatically or authenticate a supplied
change ID. File recovery plans temporarily contain mutation data in private files;
unsafe or corrupt plans refuse operation for inspection. Do not discard pending
plans to bypass recovery. Pause and upgrade all writers together; direct/old
writers cannot participate in the durable feed. Read `neosian docs changes`.
