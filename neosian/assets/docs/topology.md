---
title: Topology, not hierarchy — the four shapes
summary: Who runs neosian code, where the bytes live, and one writer per root
---

# Topology, not hierarchy

Two axes decide how you deploy neosian: **who runs neosian code** and
**where the bytes live**. Four shapes fall out — none is a hierarchy,
and the quickstart always begins embedded.

|  | bytes on local files | bytes in a database |
|---|---|---|
| **your app runs neosian** | embed + `FileStore` — dev, local tools, single-writer agents | embed + `PostgresStore` — production, multi-worker |
| **a separate process runs neosian** | the state process (`neosian serve`) owning a FileStore root | the state process over Postgres |

## Embed first

A single app embeds the library and talks to its store directly:
files for dev/local, Postgres for production. `PostgresStore` is a
**driver, not a process** — the database is your existing infra, the
SQLAlchemy shape. An embedded app needs no proxy in front of its own
store.

## The daemon is reach, not capability

The state process — `neosian serve`, the `server` extra — is the
first-class answer when state is **shared across processes, apps, or
languages** — including one container in a dev compose beside redis
and minio — or when a FileStore root needs more than one writer: one
process owns the files and every client speaks to it. It adds no
capability the library lacks, only reach. `docker run` is never step
one.

## The appliance quickstart

One token, one volume, one health check:

```bash
docker build -t neosian .          # the shipped Dockerfile
docker run -d \
  -e NEOSIAN_SERVE_TOKEN=change-me \
  -p 6367:6367 -v neosian-state:/data \
  neosian
curl -fsS http://localhost:6367/health
```

The default command serves a FileStore on the `/data` volume; set
`NEOSIAN_POSTGRES_DSN` (and override the command, e.g. `--schema
neosian`) for the Postgres backend. Without the container it is one
command: `NEOSIAN_SERVE_TOKEN=… neosian serve --root DIR`. The token
is env-only and an unset token refuses to start; TLS terminates at a
reverse proxy.

Python clients speak the store wire:

```python
from neosian import RemoteStore

store = await RemoteStore.connect("http://localhost:6367", token="change-me")
```

`RemoteStore` implements both storage ABCs over the core install (no
extra needed), so it drops into `Conversation` and `MemoryConfig`
exactly where `FileStore` does. Agents speak MCP over streamable HTTP
at `/mcp` when the server is started with mounts (`--scope` or
`--mount`).

## Who wrote what

The state process asserts identity (DESIGN §20): `NEOSIAN_SERVE_TOKEN`
may be a table — `claude-code:laptop=…,app:kit=…` — and every write
through the wire is recorded under the presenting token's client
(`<client>[/<what the client said>]`); a bare token is the one client
`client:default`. Shell entry points reach the process with `--url` and
`NEOSIAN_CLIENT_TOKEN`; `neosian audit --scope S` reads the ledger back
on any substrate, and `--url` reads it through the process.

The agent door reads as well as writes. A foreign agent's `SessionStart`
hook prints the memory index and "where we left off" — the scope's
recent sessions, log-projected — into its own window, and its MCP
client calls `recall_turn(turn, conversation)` on `/mcp` (or the stdio
server) to re-read any recorded turn verbatim: one client writes, a
different client recalls, on the same store (`neosian docs agents`,
`neosian docs mcp`).

## One writer per root

FileStore's in-process lock serializes mutations inside one process;
across processes, files cannot arbitrate — two writers on one root
can interleave a read-modify-write and lose an edit. neosian
documents the constraint instead of engineering around it: no lock
files, no `flock`, no leases (they half-promise arbitration at the
price of an NFS/Windows/containers portability matrix and a
stale-lock failure mode, on the substrate whose entire value is that
you can `cat` it).

**A root is owned by one writer at a time** — an agent's shell
(`neosian memory`), one MCP server process, or one embedding
application — while any number of readers may run beside it; a
concurrent reader is bounded to a stale read, never a corrupted
store. Multi-writer needs route to `PostgresStore`, which arbitrates
on the version-row primary key, or to the state process, where one
`neosian serve` owns the files and every client speaks to it over
`RemoteStore`.

## Choosing

- One app, one machine, inspectable state → embed + `FileStore`.
- One app, many workers or many machines → embed + `PostgresStore`.
- Many apps or languages sharing one memory, or a FileStore root that
  needs more than one writer → the state process (`neosian serve`).
