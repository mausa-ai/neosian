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
command: `NEOSIAN_SERVE_TOKEN=… neosian serve` — no flags serves the
home, `--root DIR` another root. The token is env-only and an unset
token refuses to start; TLS terminates at a reverse proxy.

## The home: one place for every project

`~/.neosian` — or `$NEOSIAN_HOME` — is the store every command and the
playground use when no flag names one, and the root a neosian agent
reaches through `home()`. Per project is a scope, not a root:
`neosian record install` and `neosian mcp install` spell this
directory's layout into the client's config (`user:<login>` at
`/user`, `user:<login>/proj:<slug>` at `/project`), and
`project_scope()` spells the same for a `Conversation`. One `neosian
audit --scope user:<login>/proj:<slug>` then lists every agent's work
in the project.

Many projects and agents on one home is the multi-writer shape, so the
answer is the state process on the home — one process owning the files,
every hook and MCP server registered with `--url`. As a per-user
service, a docs recipe, never library scope:

```xml
<!-- macOS: ~/Library/LaunchAgents/com.neosian.serve.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.neosian.serve</string>
  <key>ProgramArguments</key>
  <array><string>/path/to/venv/bin/neosian</string><string>serve</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>NEOSIAN_SERVE_TOKEN</key><string>change-me</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
```

```ini
# Linux: ~/.config/systemd/user/neosian.service
[Service]
ExecStart=/path/to/venv/bin/neosian serve
EnvironmentFile=%h/.config/neosian/serve.env   # NEOSIAN_SERVE_TOKEN=…
Restart=on-failure
[Install]
WantedBy=default.target
```

Then `launchctl load` / `systemctl --user enable --now neosian`, and
every install on the machine takes `--url http://127.0.0.1:6367` with
`NEOSIAN_CLIENT_TOKEN` in the client's own environment.

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
  needs more than one writer — the home, once two projects' hooks or
  servers write it → the state process (`neosian serve`).
