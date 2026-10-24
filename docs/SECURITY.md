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
