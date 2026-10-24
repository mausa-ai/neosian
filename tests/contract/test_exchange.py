"""One-way exchange, recovery fences and physical erasure via public interfaces."""

from __future__ import annotations

import json
import os

import pytest

from tests.contract.exchange_support import SCOPE, apply, export, files, receiver, save
from tests.contract.profile_support import run
from tests.contract.support import ADMIN, LIMITED, Binary, fixture, serve


@pytest.mark.parametrize("remote", [False, True])
def test_snapshot_excludes_history_and_lifecycle_preserves_envelope(
    binary: Binary, remote: bool
) -> None:
    with serve(binary) as server:
        doc = {
            **fixture("records.json")["document"],
            "scope": SCOPE,
            "path": "notes/a",
            "content": "Current café\r\n",
        }
        server.post(
            "store/restore_scope",
            scope=SCOPE,
            documents=[doc],
            versions=[],
            redactions=[],
        )
        server.post(
            "memory/write", scope=SCOPE, path="notes/b", content="hidden historic body"
        )
        server.post("memory/write", scope=SCOPE, path="notes/b", content="current body")
        for path in [
            "sessions/private",
            "handoff",
            "messages/private",
            "message-links/private",
        ]:
            server.post(
                "memory/write", scope=SCOPE, path=path, content="excluded lifecycle"
            )
        server.post(
            "memory/write",
            scope="user:foreign",
            path="private",
            content="excluded scope",
        )
        flags = ["--scope", SCOPE] + (["--url", server.url] if remote else [])
        snap = export(
            binary, "snapshot", flags=flags, env={"NEOSIAN_CLIENT_TOKEN": ADMIN}
        )
        content = snap.read_text()
        assert "hidden historic body" not in content and "excluded" not in content
        bundle = json.loads(content)
        assert [u["path"] for u in bundle["updates"]] == ["notes/a", "notes/b"]
        assert bundle["updates"][0]["document"] == {
            k: v for k, v in doc.items() if k not in {"scope", "path"}
        }
        verified = save(binary.directory / "independent.json", bundle)
        assert json.loads(verified.read_text())["id"] == json.loads(content)["id"]
        assert apply(binary, snap, write=False)["status"] == "preview"
        assert not files(binary)
        assert apply(binary, snap)["status"] == "applied"
        before = files(binary)
        assert apply(binary, snap)["status"] == "duplicate" and files(binary) == before
        view = binary.json("memory", "view", "/memories/notes/a", *receiver(binary))
        assert "Current café" in view["data"]
        rows = binary.json("memory", "versions", "/memories/notes/b", *receiver(binary))
        assert len(rows["versions"]) == 1 and rows["versions"][0]["version"] == 2
        if os.name != "nt":
            assert snap.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("remote", [False, True])
def test_delta_edits_rename_delete_erasure_and_no_resurrection(
    binary: Binary, remote: bool
) -> None:
    with serve(binary) as server:
        flags = ["--scope", SCOPE] + (["--url", server.url] if remote else [])
        env = {"NEOSIAN_CLIENT_TOKEN": ADMIN}
        server.post("memory/write", scope=SCOPE, path="a", content="old-private")
        snap = export(binary, "snapshot", flags=flags, env=env)
        apply(binary, snap)
        server.post("memory/write", scope=SCOPE, path="a", content="new-private")
        edit = export(binary, "edit", after=snap, flags=flags, env=env)
        server.post("memory/rename", scope=SCOPE, src="a", dst="moved")
        moved = export(binary, "moved", after=edit, flags=flags, env=env)
        refused = binary.run(
            "exchange", "apply", str(moved), *receiver(binary), "--write"
        )
        assert refused.returncode == 1 and "checkpoint_mismatch" in refused.stdout
        apply(binary, edit)
        apply(binary, moved)
        assert (
            binary.run("memory", "view", "/memories/a", *receiver(binary)).returncode
            == 1
        )
        server.post("memory/delete", scope=SCOPE, path="moved")
        deleted = export(binary, "deleted", after=moved, flags=flags, env=env)
        assert "new-private" not in deleted.read_text()
        apply(binary, deleted)
        assert b"old-private" in b"".join(files(binary).values())
        server.post("memory/redact", scope=SCOPE)
        erased = export(binary, "erased", after=deleted, flags=flags, env=env)
        apply(binary, erased)
        physical = b"".join(files(binary).values())
        assert b"old-private" not in physical and b"new-private" not in physical
        for stale in [snap, edit, moved, deleted]:
            refused = binary.run(
                "exchange", "apply", str(stale), *receiver(binary), "--write"
            )
            assert refused.returncode == 1
        assert apply(binary, erased)["status"] == "duplicate"
        assert b"private" not in b"".join(files(binary).values())
        server.post(
            "memory/write", scope=SCOPE, path="a", content="new data after erase"
        )
        again = export(binary, "again", after=erased, flags=flags, env=env)
        apply(binary, again)
        rows = binary.json("memory", "versions", "/memories/a", *receiver(binary))[
            "versions"
        ]
        assert rows[0]["content"] == "new data after erase" and all(
            r["redacted"] for r in rows[1:]
        )


def test_scope_erasure_coalesces_later_writes_and_unmodified_version(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        for path in ["a", "b", "gone"]:
            server.post("memory/write", scope=SCOPE, path=path, content="secret")
        snap = export(binary, "snapshot")
        apply(binary, snap)
        server.post("memory/delete", scope=SCOPE, path="gone")
        server.post("memory/redact", scope=SCOPE)
        server.post("memory/write", scope=SCOPE, path="a", content="after erasure")
        server.post(
            "memory/write", scope=SCOPE, path="fresh", content="created after erasure"
        )
        delta = export(binary, "delta", after=snap)
        apply(binary, delta)
        rows = binary.json("memory", "versions", "/memories/b", *receiver(binary))[
            "versions"
        ]
        assert rows[0]["version"] == 1 and rows[0]["redacted"]
        assert b"secret" not in b"".join(files(binary).values())
        assert b"after erasure" in b"".join(files(binary).values())
        trail = binary.directory / "replica/user%3Aexchange/redactions.jsonl"
        assert json.loads(trail.read_text())["count"] == 3


@pytest.mark.parametrize("mutation", ["write", "delete", "redact", "metadata", "lost"])
def test_local_edits_and_lost_checkpoint_refuse_without_changes(
    binary: Binary, mutation: str
) -> None:
    binary.json(
        "memory", "create", "/memories/a", "--content", "first", "--scope", SCOPE
    )
    snap = export(binary, "snapshot")
    apply(binary, snap)
    binary.json(
        "memory", "create", "/memories/a", "--content", "second", "--scope", SCOPE
    )
    delta = export(binary, "delta", after=snap)
    if mutation == "metadata":
        doc = binary.directory / "replica/user%3Aexchange/documents/a.md"
        doc.write_text(
            doc.read_text().replace("version: 1", "extra: changed\nversion: 1")
        )
    elif mutation == "lost":
        (binary.directory / "replica/user%3Aexchange/.exchange/receipt.json").unlink()
    else:
        args = (
            ["create", "/memories/a", "--content", "local"]
            if mutation == "write"
            else [mutation, "/memories/a"]
        )
        binary.json("memory", *args, *receiver(binary))
    before = files(binary)
    for bundle in [delta, snap]:
        result = binary.run(
            "exchange", "apply", str(bundle), *receiver(binary), "--write"
        )
        assert result.returncode == 1
    assert files(binary) == before
    status = binary.json("exchange", "status", *receiver(binary))
    assert status["status"] == (
        "snapshot_required" if mutation == "lost" else "conflict"
    )


def test_checkpoint_recovery_and_empty_delta(binary: Binary) -> None:
    binary.json("memory", "create", "/memories/a", "--content", "one", "--scope", SCOPE)
    snap = export(binary, "snapshot")
    apply(binary, snap)
    checkpoint = binary.directory / "checkpoint.json"
    checkpoint.write_text(
        binary.run("exchange", "status", *receiver(binary), "--checkpoint-only").stdout
    )
    snap.unlink()
    empty = export(binary, "empty", after=checkpoint)
    assert json.loads(empty.read_text())["updates"] == []
    apply(binary, empty)
    assert apply(binary, empty)["status"] == "duplicate"


def test_retention_gap_needs_new_snapshot_and_wrong_scope_refuses(
    binary: Binary,
) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope=SCOPE, path="a", content="old")
        snap = export(binary, "snapshot")
        apply(binary, snap)
        server.post("memory/redact", scope=SCOPE, path="a")
        head = server.post("memory/changes", scope=SCOPE)["head_cursor"]
        server.post("memory/prune_changes", scope=SCOPE, through=head, write=True)
        output = binary.directory / "delta.json"
        result = binary.run(
            "exchange",
            "delta",
            "/memories",
            str(output),
            "--after",
            str(snap),
            "--scope",
            SCOPE,
            "--paused",
        )
        assert (
            result.returncode == 1
            and "snapshot_required" in result.stdout
            and not output.exists()
        )
        fresh = export(binary, "fresh")
        assert (
            binary.run(
                "exchange", "apply", str(fresh), *receiver(binary), "--write"
            ).returncode
            == 1
        )
        assert (
            binary.run(
                "exchange",
                "apply",
                str(fresh),
                "--root",
                str(binary.directory / "other"),
                "--scope",
                "user:wrong",
                "--write",
            ).returncode
            == 1
        )


def test_read_authority_is_rechecked_on_every_export(binary: Binary) -> None:
    with serve(binary) as server:
        server.post("memory/write", scope="user:allowed", path="a", content="permitted")
        snap = export(
            binary,
            "allowed",
            flags=["--scope", "user:allowed", "--url", server.url],
            env={"NEOSIAN_CLIENT_TOKEN": LIMITED},
        )
        result = run(
            binary,
            "exchange",
            "snapshot",
            "/memories",
            str(binary.directory / "denied.json"),
            "--scope",
            SCOPE,
            "--url",
            server.url,
            "--paused",
            env={"NEOSIAN_CLIENT_TOKEN": LIMITED},
        )
        assert (
            result.returncode == 1 and not (binary.directory / "denied.json").exists()
        )
        result = run(
            binary,
            "exchange",
            "delta",
            "/memories",
            str(binary.directory / "revoked.json"),
            "--after",
            str(snap),
            "--scope",
            "user:allowed",
            "--url",
            server.url,
            "--paused",
            env={"NEOSIAN_CLIENT_TOKEN": "revoked-token"},
        )
        assert (
            result.returncode == 1 and not (binary.directory / "revoked.json").exists()
        )


@pytest.mark.parametrize(
    "case",
    [
        "format",
        "digest",
        "duplicate",
        "missing_base",
        "missing_actor",
        "missing_deletion",
        "lifecycle",
        "version",
        "time",
        "erased_content",
        "extra_reserved",
        "unknown",
        "scope",
        "case_alias",
        "path_overlap",
        "oversized",
    ],
)
def test_invalid_bundles_cannot_touch_the_receiver(binary: Binary, case: str) -> None:
    binary.json(
        "memory", "create", "/memories/a", "--content", "body", "--scope", SCOPE
    )
    snap = export(binary, "snapshot")
    bundle = json.loads(snap.read_text())
    record = bundle["updates"][0]["document"]
    if case == "format":
        bundle["format"] = 2
    elif case == "lifecycle":
        bundle["updates"][0]["path"] = "messages/unsafe"
    elif case == "version":
        record["version"] = True
    elif case == "time":
        record["updated_at"] = "2026-10-25T12:00:00"
    elif case == "erased_content":
        record["redacted"] = True
    elif case == "extra_reserved":
        record["extra"]["version"] = 2
    elif case == "unknown":
        bundle["future"] = "unsupported"
    elif case == "scope":
        bundle["checkpoint"]["scope"] = "user:foreign"
    elif case == "case_alias":
        bundle["updates"].append({**bundle["updates"][0], "path": "A"})
    elif case == "path_overlap":
        bundle["updates"].append({**bundle["updates"][0], "path": "a.md/child"})
    elif case == "oversized":
        record["content"] = "x" * (1024 * 1024 + 1)
    bad = save(binary.directory / "bad.json", bundle)
    if case.startswith("missing_"):
        field = case.removeprefix("missing_")
        target = {"base": bundle, "actor": record, "deletion": bundle["updates"][0]}[
            field
        ]
        del target[field]
        # Keep the digest of the complete object: defaulting null would accept it.
        bad.write_text(json.dumps(bundle), encoding="utf-8")
    if case == "digest":
        bad.write_text(bad.read_text().replace('"body"', '"changed"'))
    if case == "duplicate":
        bad.write_text(
            bad.read_text().replace('"format": 1', '"format": 1, "format": 1', 1)
        )
    result = binary.run("exchange", "apply", str(bad), *receiver(binary), "--write")
    assert result.returncode == 1 and not files(binary)


def test_export_needs_pause_and_tracked_scope_and_does_not_overwrite(
    binary: Binary,
) -> None:
    output = binary.directory / "snapshot.json"
    args = ["exchange", "snapshot", "/memories", str(output), "--scope", SCOPE]
    assert binary.run(*args).returncode == 1 and not binary.home.exists()
    assert binary.run(*args, "--paused").returncode == 1 and not output.exists()
    binary.json("memory", "create", "/memories/a", "--content", "one", "--scope", SCOPE)
    export(binary, "snapshot")
    before = output.read_bytes()
    assert (
        binary.run(*args, "--paused").returncode == 1 and output.read_bytes() == before
    )
