"""Current grants, mount routing, opaque remote cursors and mailbox references."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.profile_support import PROFILE, bind, grant_env, install, rpc, run
from tests.contract.support import Binary, serve
from tests.contract.test_transport import TOKEN, responder


def test_changes_and_retention_use_current_scope_authority(binary: Binary) -> None:
    grants = binary.directory / "grants.toml"
    env = grant_env(grants)
    with serve(binary, extra_env=env) as server:
        server.post(
            "memory/write", scope="workspace:portable", path="doc", content="private"
        )
        first = server.request(
            "/v1/memory/changes",
            {"scope": "workspace:portable", "limit": 1},
            token="reader-token",
        )
        assert first.status == 200
        cursor = first.body["cursor"]
        for route, body in [
            ("changes", {"scope": "workspace:other", "cursor": cursor}),
            ("changes", {"scope": "workspace:portable-more"}),
            ("prune_changes", {"scope": "workspace:portable", "through": cursor}),
            (
                "prune_changes",
                {"scope": "workspace:portable", "through": cursor, "write": True},
            ),
        ]:
            denied = server.request("/v1/memory/" + route, body, token="reader-token")
            assert denied.status == 403
            assert "private" not in json.dumps(denied.body)
        install(binary, PROFILE)
        bind(binary, "--url", server.url, "--token-env", "CHANGE_TEST_TOKEN")
        answers = rpc(
            binary,
            [("changes", {"path": "/project", "cursor": cursor})],
            env={"CHANGE_TEST_TOKEN": "reader-token"},
        )
        page = json.loads(answers[2]["result"]["content"][0]["text"])
        assert (
            page["source"] == "knowledge"
            and page["events"][0]["reference"] == "/project/doc"
        )
        assert "private" not in json.dumps(page)
        for path in ["/", "/project/doc", "/missing"]:
            answer = rpc(
                binary,
                [("changes", {"path": path})],
                env={"CHANGE_TEST_TOKEN": "reader-token"},
            )
            assert answer[2]["result"]["isError"] is True
    text = grants.read_text()
    head, reader = text.split('actor = "client:reader"')
    grants.write_text(
        head + 'actor = "client:reader"' + reader.replace("read = true", "read = false")
    )
    with serve(binary, extra_env=env) as server:
        denied = server.request(
            "/v1/memory/changes",
            {"scope": "workspace:portable", "cursor": cursor},
            token="reader-token",
        )
        assert denied.status == 403


def remote_page(**changes: Any) -> dict[str, Any]:
    return {
        "format": 1,
        "scope": "user:test",
        "stream": "11111111-2222-4333-8444-555555555555",
        "events": [],
        "cursor": "opaque:checkpoint",
        "head_cursor": "opaque:checkpoint",
        "earliest_cursor": "opaque:floor",
        "gap": None,
        "has_more": False,
        **changes,
    }


def test_remote_change_cursors_pass_through_without_decoding(binary: Binary) -> None:
    opaque = "🚀" * 512

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "change_feed": {"version": 1}}
        assert body["scope"] == "user:test"
        if path == "/v1/memory/prune_changes":
            assert body["through"] == opaque
            return 200, {
                "format": 1,
                "scope": "user:test",
                "removed": 0,
                "write": False,
                "earliest_cursor": opaque,
            }
        assert path == "/v1/memory/changes"
        assert body["cursor"] in [None, opaque]
        return 200, remote_page(cursor=opaque)

    with responder(reply) as (url, requests):
        flags = ["--url", url, "--scope", "user:test"]
        for extra in [[], ["--cursor", opaque]]:
            found = run(
                binary,
                "changes",
                "read",
                *flags,
                *extra,
                env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
            )
            assert found.returncode == 0, found.stderr
            assert json.loads(found.stdout)["cursor"] == opaque
        pruned = run(
            binary,
            "changes",
            "prune",
            *flags,
            "--through",
            opaque,
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert pruned.returncode == 0, pruned.stderr
        assert len(requests) == 6


@pytest.mark.parametrize(
    "altered",
    [
        {"format": 2},
        {"scope": "user:other"},
        {"has_more": True},
        {"gap": "silent"},
        {"cursor": "x" * 2049},
        {"stream": None},
        {"unexpected": "field"},
        {"gap": "untracked"},
        {"events": [{"kind": "memory_write"}]},
    ],
)
def test_remote_malformed_pages_are_refused(
    binary: Binary, altered: dict[str, Any]
) -> None:
    def reply(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        return (
            (200, {"wire_version": 6, "change_feed": {"version": 1}})
            if path == "/v1/capabilities"
            else (200, remote_page(**altered))
        )

    with responder(reply) as (url, _requests):
        result = run(
            binary,
            "changes",
            "read",
            "--url",
            url,
            "--scope",
            "user:test",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert result.returncode == 1


def test_undeclared_changes_refuse_without_posting(binary: Binary) -> None:
    def reply(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        assert path == "/v1/capabilities"
        return 200, {"wire_version": 6}

    with responder(reply) as (url, requests):
        result = run(
            binary,
            "changes",
            "read",
            "--url",
            url,
            "--scope",
            "user:test",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert result.returncode == 1 and len(requests) == 1


def test_mailbox_references_preserve_existing_claim_fences(binary: Binary) -> None:
    flags = ["--scope", "user:test", "--session", "worker"]
    binary.json(
        "memory",
        "create",
        "/memories/doc",
        "--scope",
        "user:test",
        "--content",
        "private-context",
    )
    change = json.loads(binary.run("changes", "read", "--scope", "user:test").stdout)[
        "events"
    ][-1]

    def command(action: str, *args: str) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            binary.json("messages", action, *flags, *args)["data"]
        )
        return result

    sent = command(
        "send",
        "--body",
        "Review",
        "--actionable",
        "--context",
        "/memories/doc",
        "--context-version",
        "1",
        "--change-id",
        change["id"],
    )
    assert sent["context"] == {
        "scope": "user:test",
        "path": "doc",
        "version": 1,
        "change_id": change["id"],
    }
    assert "private-context" not in json.dumps(sent)
    identity = ["--message-id", sent["id"], "--occurrence", "1"]
    claim = command("claim", *identity)
    token = claim["claim_token"]
    command("renew", *identity, "--token", token)
    command("release", *identity, "--token", token)
    again = command("claim", *identity)
    stale = binary.run(
        "messages",
        "complete",
        *flags,
        *identity,
        "--token",
        token,
        "--outcome",
        "stale",
    )
    assert stale.returncode == 1
    complete = command(
        "complete", *identity, "--token", again["claim_token"], "--outcome", "Reviewed"
    )
    assert complete["context"] == sent["context"]
    binary.json(
        "memory",
        "create",
        "/memories/doc",
        "--scope",
        "user:test",
        "--content",
        "changed",
    )
    for args in [
        ["--context", "/memories/doc", "--context-version", "1"],
        ["--context", "/outside/doc"],
        ["--change-id", change["id"]],
    ]:
        assert (
            binary.run(
                "messages", "send", *flags, "--body", "invalid", *args
            ).returncode
            == 1
        )
    binary.json("memory", "redact", "/memories/doc", "--scope", "user:test")
    assert (
        binary.run(
            "messages", "send", *flags, "--body", "erased", "--context", "/memories/doc"
        ).returncode
        == 1
    )
    assert command("view", "--message-id", sent["id"])["context"] == sent["context"]
