---
title: "Local, Postgres or a state process"
summary: Run state on your own storage and connect clients through an authenticated wire
---

# Local, Postgres or a state process

For one authorized workspace across machines, use a portable profile and
machine-local bindings (`neosian docs profiles`). Share the exported selection,
then bind each machine to the same granted HTTP service with its own token.
The operator enables exact-scope/conversation grants; agent accounts are not
authorization. Each client runs a local stdio MCP bridge. Constrained hosted
HTTP MCP remains unavailable. Capture starts off, with explicit local history
or shared-history consent; local capture can accompany remote knowledge.

One binary provides local CLI, MCP and HTTP access. FileStore uses a directory;
Postgres uses an operator-owned schema; remote clients use the same wire from
any language. No hosted service, model account or Python runtime is needed.

```sh
neosian memory view / --root ./state --scope user:demo
neosian schema postgres --schema neosian > schema.sql
# Review schema.sql, then apply it with your own database connection.
NEOSIAN_POSTGRES_DSN='postgresql://user:password@host/database?sslmode=require' neosian memory view / --scope user:demo
```

DSNs are environment-only. Read `neosian docs migration` for supported
connection options and verified TLS. Use an explicit host/user/database;
libpq service files and implicit PG environment defaults are not consumed.

## Serve and connect

```sh
NEOSIAN_SERVE_TOKEN='your-private-token' neosian serve --root ./state --scope user:demo
NEOSIAN_CLIENT_TOKEN='your-private-token' neosian memory view / --url http://127.0.0.1:6367 --scope user:demo
```

The default bind is `127.0.0.1:6367`. Tokens are required, and never supplied
on argv. `/health` is unauthenticated liveness only. Terminate TLS at a reverse
proxy for network use; the server does not terminate TLS itself. HTTPS clients
verify trust and hostname, with `SSL_CERT_FILE` for a private CA bundle.

A token table `client:alice=token-a,client:bob=token-b` attributes writes to
individual clients. `client:alice@user:alice+alice-=token-a` restricts memory
scope and conversation ID by literal prefix. These are prefixes, not exact
identity equality. Constrained tokens cannot use whole-store portability,
store-wide history searches or `/mcp`. A token without an allowance can access
all stored data. Restores preserve original actors rather than stamping the
importing client. See `neosian docs wire` before sharing a store.

## Container

```sh
docker run -e NEOSIAN_SERVE_TOKEN='your-private-token' -p 6367:6367 -v state:/data ghcr.io/mausa-ai/neosian:2.1.0
```

The image serves the Rust binary as uid 1000 with a private `/data` volume.
It supports FileStore and Postgres and contains no Python runtime. Pass
`NEOSIAN_POSTGRES_DSN` and override the default arguments with `--schema NAME`
when selecting Postgres. Schema application is a separate operator action.
The image has a health check and drains requests on SIGTERM.

For FileStore, coordinate local writers using the shared root lock and stop
writers for migrations or consistent archives. Network filesystems are outside
that guarantee. Postgres supports distributed writers. Neither remote access
nor a shared token automatically makes recordings private; select storage and
sharing boundaries intentionally.
