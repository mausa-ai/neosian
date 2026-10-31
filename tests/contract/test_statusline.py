"""Cache visibility consumes only a documented host payload, without storage."""

from __future__ import annotations

import json
import time

import pytest

from tests.contract.support import Binary


def test_statusline_normalizes_without_opening_storage(binary: Binary) -> None:
    expiry = int(time.time()) + 300
    payload = {
        "prompt_cache": {
            "warm": True,
            "caching_observed": True,
            "ttl": "5m",
            "expires_at": expiry,
            "recache_tokens_if_cold": 12345,
            "requests": 2,
            "misses": 1,
        },
        "context_window": {
            "current_usage": {
                "cache_read_input_tokens": 1623,
                "cache_creation_input_tokens": 16648,
            }
        },
        "session_id": "private-session",
        "workspace": {"current_dir": "private-path"},
    }
    # This command must work even when memory selection is broken.
    binary.home.write_text("not a store", encoding="utf-8")
    result = binary.run(
        "statusline", "--client", "claude-code", "--json", stdin=json.dumps(payload)
    )
    assert result.returncode == 0, result.stderr
    normalized = json.loads(result.stdout)
    assert normalized.pop("remaining_seconds") in range(295, 301)
    assert normalized == {
        "state": "warm",
        "ttl": "5m",
        "expires_at": expiry,
        "recache_tokens_if_cold": 12345,
        "last_main_cache_read_tokens": 1623,
        "last_main_cache_write_tokens": 16648,
        "main_requests": 2,
        "main_misses": 1,
    }
    assert binary.home.read_text(encoding="utf-8") == "not a store"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "cache unknown"),
        ({"prompt_cache": None}, "cache unknown"),
        ({"prompt_cache": {"warm": False}}, "cache estimate cold"),
        (
            {"prompt_cache": {"warm": True, "expires_at": 1}},
            "cache estimate cold",
        ),
        (
            {"prompt_cache": {"warm": False, "caching_observed": False}},
            "cache unknown",
        ),
        (
            {"prompt_cache": {"warm": True}},
            "cache estimate warm (expiry unknown)",
        ),
        (
            {
                "prompt_cache": {
                    "warm": True,
                    "requests": 2,
                    "misses": 1,
                    "cache_write_tokens": 9999999,
                },
                "context_window": {
                    "current_usage": {
                        "cache_read_input_tokens": 1623,
                        "cache_creation_input_tokens": 16648,
                    }
                },
            },
            "cache estimate warm (expiry unknown)"
            " | last main read 1623, wrote 16648 | misses 1",
        ),
        (
            {
                "prompt_cache": {
                    "warm": "\u001b[2J",
                    "ttl": "\u001b[2J",
                    "recache_tokens_if_cold": -1,
                    "requests": -1,
                    "misses": "\u001b[2J",
                },
                "context_window": {
                    "current_usage": {
                        "cache_read_input_tokens": "\u001b[2J",
                        "cache_creation_input_tokens": -1,
                    }
                },
            },
            "cache unknown",
        ),
    ],
)
def test_statusline_observation_states(
    binary: Binary, payload: dict[str, object], expected: str
) -> None:
    result = binary.run(
        "statusline", "--client", "claude-code", stdin=json.dumps(payload)
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected + "\n"
    assert not binary.home.exists()


@pytest.mark.parametrize(
    "payload",
    ["secret-invalid", "[]", "null", " " * (2**20 + 1)],
    ids=["malformed", "array", "null", "oversized"],
)
def test_statusline_refuses_invalid_payload_without_echo(
    binary: Binary, payload: str
) -> None:
    result = binary.run("statusline", "--client", "claude-code", stdin=payload)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "statusline: expected a JSON object of at most 1 MiB\n"
    assert not binary.home.exists()
