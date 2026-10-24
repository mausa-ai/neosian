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
