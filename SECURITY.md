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
(`neosian version`), the transport involved (library, shell, MCP, the
state process), and a reproduction.

## Supported versions

The newest annotated `v<X.Y.Z>` release tag receives fixes. Once
`v1.0.0` carries the stability promise (README, "Stability"), the
newest 1.x release is the supported line; earlier 0.x tags are not
patched.

## What the design already commits to

Read these before reporting; several "findings" are rulings:

- **Secrets never ride argv.** API keys, the serve token and the
  Postgres DSN are read from the environment only (`NEOSIAN_SERVE_TOKEN`,
  `NEOSIAN_CLIENT_TOKEN`, `NEOSIAN_POSTGRES_DSN`, the provider keys).
  [docs/SERVICES.md](docs/SERVICES.md) lists every key and what turning
  it off means.
- **The state process refuses to start without a token**, `/health` is
  its one unauthenticated route, and TLS terminates at a reverse proxy
  in front of it. The token may be a per-client table so the ledger
  records *who* wrote.
- **Redaction is the one eraser.** Every memory write appends a
  full-content version row; `neosian memory redact` clears content
  everywhere while keeping the audit skeleton. A report that a deleted
  document is still readable in its history is the design working;
  a report that redaction left content behind is a vulnerability.
- **Tool calls are validated before the body runs**, and every call can
  be routed through the approval gate (`ToolGateConfig`); a missing
  decision is always a denial.

The neosian.com service publishes its own policy and addresses; a
report that concerns both is welcome here.
