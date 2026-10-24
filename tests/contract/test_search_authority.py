"""Raw authority checks and an independently implemented opaque-cursor service."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.contract.profile_support import PROFILE, bind, grant_env, install, rpc, run
from tests.contract.support import Binary, serve
from tests.contract.test_transport import TOKEN, responder


def test_search_checks_grants_before_scanning_or_resuming(binary: Binary) -> None:
    grants = binary.directory / "grants.toml"
    env = grant_env(grants)
    with serve(binary, extra_env=env) as server:
        for scope, path in [
            ("workspace:portable", "a"),
            ("workspace:portable", "z"),
            ("workspace:other", "secret"),
        ]:
            server.post(
                "memory/write", scope=scope, path=path, content="needle " + scope
            )
        first = server.request(
            "/v1/memory/search",
            {
                "scope": "workspace:portable",
                "query": "needle",
                "limit": 1,
            },
            token="reader-token",
        )
        assert first.status == 200 and first.body["hits"][0]["path"] == "a"
        cursor = first.body["next_cursor"]
        for body in [
            {"scope": "workspace:other", "query": "needle"},
            {"scope": "workspace:other", "query": "needle", "cursor": cursor},
            {"query": "needle", "conversation_id": "other-1"},
            {"query": "needle", "conversations": []},
            {"scope": "workspace:portable-more", "query": "needle"},
        ]:
            denied = server.request("/v1/memory/search", body, token="reader-token")
            assert denied.status == 403 and denied.body["error"]["code"] == "forbidden"
            assert "secret" not in json.dumps(denied.body)
        forged = server.request(
            "/v1/memory/search",
            {
                "scope": "workspace:portable",
                "query": "needle",
                "scopes": ["workspace:other"],
            },
            token="reader-token",
        )
        assert forged.status == 400 and forged.body["error"]["code"] == "value_error"
        install(binary, PROFILE)
        bind(binary, "--url", server.url, "--token-env", "SEARCH_TEST_TOKEN")
        answers = rpc(
            binary,
            [("search_memory", {"query": "needle"})],
            env={"SEARCH_TEST_TOKEN": "reader-token"},
        )
        hits = json.loads(answers[2]["result"]["content"][0]["text"])["hits"]
        assert [h["path"] for h in hits] == ["a", "z"]
        install(binary, PROFILE.replace("workspace:portable", "workspace:other"))
        forged_profile = run(
            binary,
            "memory",
            "search",
            "needle",
            "--profile",
            "portable",
            "--json",
            env={"SEARCH_TEST_TOKEN": "reader-token"},
        )
        assert forged_profile.returncode == 1
        assert "secret" not in forged_profile.stdout
    # A previously issued continuation cannot preserve revoked scope authority.
    text = grants.read_text(encoding="utf-8")
    head, reader = text.split('actor = "client:reader"')
    grants.write_text(
        head
        + 'actor = "client:reader"'
        + reader.replace("read = true", "read = false"),
        encoding="utf-8",
    )
    with serve(binary, extra_env=env) as server:
        denied = server.request(
            "/v1/memory/search",
            {"scope": "workspace:portable", "query": "needle", "cursor": cursor},
            token="reader-token",
        )
        assert denied.status == 403


def test_legacy_token_scope_allowances_cover_memory_search(binary: Binary) -> None:
    with serve(
        binary, extra_env={"NEOSIAN_SERVE_TOKEN": "client:limited@user:mine=limited"}
    ) as server:
        for scope, status in [("user:mine", 200), ("user:theirs", 403)]:
            reply = server.request(
                "/v1/memory/search",
                {"scope": scope, "query": "needle"},
                token="limited",
            )
            assert reply.status == status
        for body in [{"query": "needle"}, {"query": "needle", "conversations": []}]:
            reply = server.request("/v1/memory/search", body, token="limited")
            assert reply.status in (400, 403)
            assert "hits" not in reply.body


def hit(path: str = "a", **changes: Any) -> dict[str, Any]:
    return {
        "scope": "user:test",
        "path": path,
        "version": 7,
        "created_at": "2026-10-24T10:00:00Z",
        "updated_at": "2026-10-24T10:00:00Z",
        "actor": "import:independent",
        "matched_fields": ["content"],
        "excerpt": {
            "field": "content",
            "metadata_key": None,
            "offset": 0,
            "text": "needle",
        },
        **changes,
    }


@pytest.mark.parametrize(
    "backend_cursor",
    ["database-cursor:second", "\x00" * 4096, "\U0001f680" * 1024],
    ids=["ordinary", "escaped-at-byte-limit", "unicode-at-byte-limit"],
)
def test_custom_backend_cursors_remain_opaque_through_mounted_pages(
    binary: Binary, backend_cursor: str
) -> None:
    observed: list[dict[str, Any]] = []

    def reply(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "memory_search": {"version": 1}}
        assert path == "/v1/memory/search"
        observed.append(body)
        assert body["scope"] == "user:test" and body["limit"] == 1
        if body["cursor"] is None:
            return 200, {"hits": [hit()], "next_cursor": backend_cursor}
        assert body["cursor"] == backend_cursor
        return 200, {"hits": [hit("b")], "next_cursor": None}

    with responder(reply) as (url, requests):
        flags = ["--url", url, "--scope", "user:test", "--limit", "1", "--json"]
        first = run(
            binary,
            "memory",
            "search",
            "needle",
            *flags,
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert first.returncode == 0, first.stderr
        page = json.loads(first.stdout)
        assert len(page["next_cursor"].encode()) <= 8192
        second = run(
            binary,
            "memory",
            "search",
            "needle",
            *flags,
            "--cursor",
            page["next_cursor"],
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["hits"][0]["reference"] == "/memories/b"
        assert json.loads(second.stdout)["next_cursor"] is None
        assert len(observed) == 2
        assert requests == ["/v1/capabilities", "/v1/memory/search"] * 2


@pytest.mark.parametrize("capability", [None, {"version": 2}])
def test_unsupported_remote_search_never_falls_back_to_scanning(
    binary: Binary, capability: Any
) -> None:
    def reply(_path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        return 200, {"wire_version": 6, "memory_search": capability}

    with responder(reply) as (url, requests):
        result = run(
            binary,
            "memory",
            "search",
            "needle",
            "--scope",
            "user:test",
            "--url",
            url,
            "--json",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert (
            result.returncode == 1 and "does not support memory-search" in result.stderr
        )
        assert requests == ["/v1/capabilities"]


@pytest.mark.parametrize(
    "page",
    [
        {"hits": [hit(scope="user:foreign")], "next_cursor": None},
        {"hits": [hit(path="../outside")], "next_cursor": None},
        {"hits": [hit(path="sessions/private")], "next_cursor": None},
        {"hits": [hit(reference="/private/doc")], "next_cursor": None},
        {"hits": [hit(source="private")], "next_cursor": None},
        {"hits": [hit(version=0)], "next_cursor": None},
        {"hits": [hit(matched_fields=["content", "content"])], "next_cursor": None},
        {"hits": [hit(created_at="2026-10-24T10:00:00")], "next_cursor": None},
        {
            "hits": [
                hit(
                    excerpt={
                        "field": "metadata",
                        "metadata_key": "secret",
                        "offset": 0,
                        "text": "private",
                    }
                )
            ],
            "next_cursor": None,
        },
        {
            "hits": [
                hit(
                    excerpt={
                        "field": "content",
                        "metadata_key": None,
                        "offset": 0,
                        "text": "x" * 241,
                    }
                )
            ],
            "next_cursor": None,
        },
        {"hits": [hit("z"), hit("a")], "next_cursor": None},
        {"hits": [], "next_cursor": "loop"},
    ],
)
def test_malformed_remote_results_are_refused_before_output(
    binary: Binary, page: dict[str, Any]
) -> None:
    def reply(path: str, _body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"wire_version": 6, "memory_search": {"version": 1}}
        return 200, page

    with responder(reply) as (url, _requests):
        result = run(
            binary,
            "memory",
            "search",
            "needle",
            "--scope",
            "user:test",
            "--url",
            url,
            "--json",
            env={"NEOSIAN_CLIENT_TOKEN": TOKEN},
        )
        assert result.returncode == 1 and "malformed memory-search" in result.stderr
        assert "hits" not in json.loads(result.stdout)
