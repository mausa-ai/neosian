"""FileStore — the file-substrate reference implementation (DESIGN §8).

On-disk layout, one directory per scope segment (percent-encoded, so
every component carries a `%3A` and can never collide with the reserved
names):

    root/user%3A123/proj%3Aerp/
        documents/notes/api.md      # envelope frontmatter + verbatim body
        versions/notes/api.jsonl    # one row per mutation, full content
        redactions.jsonl            # {ts, actor, path|null, count} per redact

Sync I/O inside async methods (KB-scale files; the retired
FileBlackboard's precedent) — except the three that walk a whole scope
(`list_documents`, `history`, `redact`), which run in a worker thread so
they never stall the daemon's loop (IN-13; §18 calls the FileStore leg a
low-concurrency appliance for the rest). An asyncio.Lock serializes
mutations, so read-your-writes holds within a process. Across processes files cannot arbitrate — last
writer wins, which is why `supports_optimistic_concurrency` stays False
even though `expected_version` is honored best-effort in-process — and
why §8 rules one writer per root (multi-writer needs route to
PostgresStore or the state daemon).
Durability between the sidecar append and the document write is not
transactional (C1 permits); the sidecar is written first and fsync'd, so
a crash can never make a version number get reused. Everything lands
private — files 0600, directories 0700 (ledger #126).
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from neosian._foundation.conversation.file_turns import FileTurnStore
from neosian._foundation.memory import file_layout as layout, journal
from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.envelope import Envelope, parse, render
from neosian._foundation.memory.file_portable import FilePortableStore
from neosian._foundation.memory.paths import validate_document_path
from neosian._foundation.memory.scope import Scope, parse_scope
from neosian._foundation.memory.types import (
    MemoryDocument,
    MemoryEntry,
    MemoryRedaction,
    MemoryVersion,
)
from neosian._foundation.shared.clock import Clock, SystemClock
from neosian._foundation.shared.exceptions import (
    MemoryConflictError,
    MemoryDocumentNotFoundError,
    MemoryPathInvalidError,
)
from neosian._foundation.shared.fileio import private_mkdir

if TYPE_CHECKING:
    from datetime import datetime

_DOCUMENTS = layout.DOCUMENTS
_VERSIONS = layout.VERSIONS
_REDACTIONS = layout.REDACTIONS


class FileStore(MemoryStore, FileTurnStore, FilePortableStore):
    """Markdown + frontmatter memory store over a plain directory —
    implementing both storage seams (§8 documents, §9 turns) and the
    mobility protocol (§26)."""

    def __init__(self, root: str | Path, *, clock: Clock | None = None) -> None:
        self._root = Path(root).resolve()
        private_mkdir(self._root)
        self._clock = clock if clock is not None else SystemClock()
        self._lock = asyncio.Lock()
        # MC-13: scopes whose on-disk spelling this instance has checked.
        self._verified_scopes: set[str] = set()

    async def read(self, scope: str, path: str) -> MemoryDocument | None:
        scope = self._scope(scope)
        validate_document_path(path)
        doc_file = self._doc_file(scope, path)
        if not doc_file.is_file():
            return None
        return self._load(scope, path, doc_file)

    async def write(
        self,
        scope: str,
        path: str,
        content: str,
        *,
        actor: str | None = None,
        expected_version: int | None = None,
    ) -> MemoryDocument:
        scope = self._scope(scope)
        validate_document_path(path)
        async with self._lock:
            doc_file = self._doc_file(scope, path)
            existing = self._load(scope, path, doc_file) if doc_file.is_file() else None
            if expected_version is not None:
                if existing is None:
                    raise MemoryConflictError(
                        scope,
                        path,
                        "document_absent",
                        expected_version=expected_version,
                    )
                if existing.version != expected_version:
                    raise MemoryConflictError(
                        scope,
                        path,
                        "version_mismatch",
                        expected_version=expected_version,
                        actual_version=existing.version,
                    )
            rows = self._rows(scope, path)
            version = journal.next_version(rows)
            if existing is not None:
                # A hand-planted document may have no sidecar; never issue
                # a number at or below the version it already declares.
                version = max(version, existing.version + 1)
            now = self._now()
            row = MemoryVersion(
                path=path,
                version=version,
                action="modified" if existing is not None else "created",
                content=content,
                actor=actor,
                created_at=now,
            )
            journal.append_row(self._journal_file(scope, path), row)
            envelope = Envelope(
                version=version,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                actor=actor,
                extra=existing.extra if existing is not None else {},
            )
            journal.atomic_write(doc_file, render(envelope, content))
            return MemoryDocument(
                scope=scope,
                path=path,
                content=content,
                version=version,
                created_at=envelope.created_at,
                updated_at=now,
                actor=actor,
                extra=MappingProxyType(dict(envelope.extra)),
            )

    async def delete(self, scope: str, path: str, *, actor: str | None = None) -> bool:
        scope = self._scope(scope)
        validate_document_path(path)
        async with self._lock:
            doc_file = self._doc_file(scope, path)
            if not doc_file.is_file():
                return False
            existing = self._load(scope, path, doc_file)
            rows = self._rows(scope, path)
            row = MemoryVersion(
                path=path,
                version=max(journal.next_version(rows), existing.version + 1),
                action="deleted",
                content=existing.content,
                actor=actor,
                created_at=self._now(),
                redacted=existing.redacted,
            )
            journal.append_row(self._journal_file(scope, path), row)
            doc_file.unlink()
            return True

    async def rename(
        self, scope: str, src: str, dst: str, *, actor: str | None = None
    ) -> MemoryDocument:
        scope = self._scope(scope)
        validate_document_path(src)
        validate_document_path(dst)
        async with self._lock:
            src_file = self._doc_file(scope, src)
            if not src_file.is_file():
                raise MemoryDocumentNotFoundError(scope, src)
            dst_file = self._doc_file(scope, dst)
            if dst_file.is_file():
                raise MemoryConflictError(scope, dst, "destination_exists")
            existing = self._load(scope, src, src_file)
            now = self._now()
            src_row = MemoryVersion(
                path=src,
                version=max(
                    journal.next_version(self._rows(scope, src)),
                    existing.version + 1,
                ),
                action="deleted",
                content=existing.content,
                actor=actor,
                created_at=now,
                redacted=existing.redacted,
            )
            dst_version = journal.next_version(self._rows(scope, dst))
            dst_row = MemoryVersion(
                path=dst,
                version=dst_version,
                action="created",
                content=existing.content,
                actor=actor,
                created_at=now,
                redacted=existing.redacted,
            )
            journal.append_row(self._journal_file(scope, src), src_row)
            journal.append_row(self._journal_file(scope, dst), dst_row)
            envelope = Envelope(
                version=dst_version,
                created_at=now,
                updated_at=now,
                actor=actor,
                redacted=existing.redacted,
                extra=existing.extra,
            )
            journal.atomic_write(dst_file, render(envelope, existing.content))
            src_file.unlink()
            return MemoryDocument(
                scope=scope,
                path=dst,
                content=existing.content,
                version=dst_version,
                created_at=now,
                updated_at=now,
                actor=actor,
                redacted=existing.redacted,
                extra=MappingProxyType(dict(existing.extra)),
            )

    async def list_documents(
        self, scope: str, *, prefix: str = ""
    ) -> tuple[MemoryEntry, ...]:
        scope = self._scope(scope)
        return await asyncio.to_thread(self._list_documents, scope, prefix)

    def _list_documents(self, scope: Scope, prefix: str) -> tuple[MemoryEntry, ...]:
        docs_dir = self._scope_dir(scope) / _DOCUMENTS
        if not docs_dir.is_dir():
            return ()
        entries: list[MemoryEntry] = []
        for doc_file in docs_dir.rglob("*.md"):
            logical = self._logical_path(docs_dir, doc_file)
            if logical is None or not logical.startswith(prefix):
                continue
            document = self._load(scope, logical, doc_file)
            entries.append(
                MemoryEntry(
                    path=logical,
                    version=document.version,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                    redacted=document.redacted,
                )
            )
        entries.sort(key=lambda entry: entry.path)
        return tuple(entries)

    async def versions(
        self, scope: str, path: str, *, limit: int = 50
    ) -> tuple[MemoryVersion, ...]:
        scope = self._scope(scope)
        validate_document_path(path)
        if limit < 0:
            raise ValueError("limit must be >= 0")
        rows = self._rows(scope, path)
        return tuple(reversed(rows))[:limit]

    async def redact(
        self, scope: str, *, path: str | None = None, actor: str | None = None
    ) -> int:
        scope = self._scope(scope)
        if path is not None:
            validate_document_path(path)
        async with self._lock:
            return await asyncio.to_thread(self._redact, scope, path, actor)

    def _redact(self, scope: Scope, path: str | None, actor: str | None) -> int:
        targets = self._redact_targets(scope, path)
        for target in targets:
            journal_file = self._journal_file(scope, target)
            rows = journal.read_rows(journal_file, scope=scope, path=target)
            if rows:
                journal.rewrite_rows(
                    journal_file,
                    [
                        dataclasses.replace(row, content="", redacted=True)
                        for row in rows
                    ],
                )
            doc_file = self._doc_file(scope, target)
            if doc_file.is_file():
                document = self._load(scope, target, doc_file)
                envelope = Envelope(
                    version=document.version,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                    actor=document.actor,
                    redacted=True,
                    extra=document.extra,
                )
                journal.atomic_write(doc_file, render(envelope, ""))
        if targets:
            journal.append_redaction(
                self._scope_dir(scope) / _REDACTIONS,
                MemoryRedaction(
                    path=path,
                    actor=actor,
                    created_at=self._now(),
                    count=len(targets),
                ),
            )
        return len(targets)

    async def history(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryVersion, ...]:
        scope = self._scope(scope)
        journal.since_window(since, limit)
        return await asyncio.to_thread(self._history, scope, since, limit)

    def _history(
        self, scope: Scope, since: datetime | None, limit: int | None
    ) -> tuple[MemoryVersion, ...]:
        rows: list[MemoryVersion] = []
        for logical in journal.logical_paths(self._scope_dir(scope) / _VERSIONS):
            rows.extend(
                row
                for row in self._rows(scope, logical)
                if since is None or row.created_at >= since
            )
        return tuple(journal.newest_first(rows))[:limit]

    async def redactions(
        self,
        scope: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRedaction, ...]:
        scope = self._scope(scope)
        journal.since_window(since, limit)
        acts = journal.read_redactions(
            self._scope_dir(scope) / _REDACTIONS, scope=scope
        )
        recent = [
            act for act in reversed(acts) if since is None or act.created_at >= since
        ]
        return tuple(recent)[:limit]

    # Internal plumbing ----------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock returned a naive datetime (ECOSYSTEM §9)")
        return now

    def _scope(self, scope: str) -> Scope:
        """Validate a scope, then refuse a case fold once per scope (MC-13).

        The grammar admits mixed-case ids and never normalizes
        (ECOSYSTEM §2), so on a case-folding filesystem two scopes would
        share a directory and a version counter. The check is one listing
        per scope per instance — the in-process guarantee FileStore
        already makes for `expected_version`, and the same one: across
        processes files cannot arbitrate.
        """
        parsed = parse_scope(scope)
        if parsed not in self._verified_scopes:
            collided = layout.scope_case_collision(self._root, parsed)
            if collided is not None:
                raise MemoryConflictError(parsed, None, "case_collision")
            self._verified_scopes.add(parsed)
        return parsed

    def _scope_dir(self, scope: str) -> Path:
        return layout.scope_dir(self._root, scope)

    def _doc_file(self, scope: str, path: str) -> Path:
        return layout.doc_file(self._root, scope, path)

    def _journal_file(self, scope: str, path: str) -> Path:
        return layout.journal_file(self._root, scope, path)

    def _rows(self, scope: str, path: str) -> tuple[MemoryVersion, ...]:
        rows = journal.read_rows(
            self._journal_file(scope, path), scope=scope, path=path
        )
        # MC-13, for free: every row records the spelling it was written
        # under, so a case-folding filesystem hands back another path's
        # history. Refusing here is enough — the first write raises, so a
        # second document at the folded name never comes into being.
        if rows and rows[0].path != path:
            raise MemoryConflictError(scope, path, "case_collision")
        return rows

    def _load(self, scope: str, path: str, doc_file: Path) -> MemoryDocument:
        # Path.read_text(newline=) is 3.13+; the floor is 3.12.
        with doc_file.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        envelope, content = parse(text, scope=scope, path=path)
        return MemoryDocument(
            scope=scope,
            path=path,
            content=content,
            version=envelope.version,
            created_at=envelope.created_at,
            updated_at=envelope.updated_at,
            actor=envelope.actor,
            redacted=envelope.redacted,
            extra=MappingProxyType(dict(envelope.extra)),
        )

    def _logical_path(self, docs_dir: Path, doc_file: Path) -> str | None:
        relative = doc_file.relative_to(docs_dir)
        name = relative.name[: -len(".md")]
        logical = "/".join((*relative.parts[:-1], name))
        try:
            validate_document_path(logical)
        except MemoryPathInvalidError:
            return None  # never written by this store — not ours, skip
        return logical

    def _redact_targets(self, scope: str, path: str | None) -> list[str]:
        scope_dir = self._scope_dir(scope)
        if path is not None:
            has_history = self._journal_file(scope, path).is_file()
            has_document = self._doc_file(scope, path).is_file()
            return [path] if has_history or has_document else []
        matched = set(journal.logical_paths(scope_dir / _VERSIONS))
        docs_dir = scope_dir / _DOCUMENTS
        if docs_dir.is_dir():
            for doc_file in docs_dir.rglob("*.md"):
                doc_logical = self._logical_path(docs_dir, doc_file)
                if doc_logical is not None:
                    matched.add(doc_logical)
        return sorted(matched)
