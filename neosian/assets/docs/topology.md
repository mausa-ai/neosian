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
| **a separate process runs neosian** | the state daemon owning a FileStore root | the state daemon over Postgres |

## Embed first

A single app embeds the library and talks to its store directly:
files for dev/local, Postgres for production. `PostgresStore` is a
**driver, not a process** — the database is your existing infra, the
SQLAlchemy shape. An embedded app needs no proxy in front of its own
store.

## The daemon is reach, not capability

The state daemon (planned as the arc's capstone phase, **not yet
shipped**) is the first-class answer when state is **shared across
processes, apps, or languages** — including one container in a dev
compose beside redis and minio — or when a FileStore root needs more
than one writer: one process owns the files and every client speaks
to it. It adds no capability the library lacks, only reach.
`docker run` is never step one.

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
on the version-row primary key, or to the state daemon once it ships.

## Choosing

- One app, one machine, inspectable state → embed + `FileStore`.
- One app, many workers or many machines → embed + `PostgresStore`.
- Many apps or languages sharing one memory, or a FileStore root that
  needs more than one writer → the daemon shape (until it ships:
  Postgres).
