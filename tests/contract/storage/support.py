"""A unique test namespace and explicit wire calls, not an SDK implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tests.contract.schema import validate
from tests.contract.support import Reply, Server


@dataclass(frozen=True)
class Storage:
    server: Server
    peer: Server
    namespace: str
    capabilities: dict[str, Any]
    portable: bool
    # Whether this run started the service itself (file or Postgres fixture).
    # Checks that depend on the deployment, not on the wire, run only then.
    local: bool = True

    @property
    def scope(self) -> str:
        return "test:" + self.namespace

    @property
    def conversation(self) -> str:
        return self.namespace

    @property
    def client(self) -> str:
        return str(self.capabilities["client"])

    def memory(self, command: str, **body: Any) -> Any:
        return self.server.post("memory/" + command, scope=self.scope, **body)

    def turns(self, command: str, **body: Any) -> Any:
        return self.server.post(
            "conversation/" + command, conversation_id=self.conversation, **body
        )

    def search(self, query: str, **body: Any) -> Any:
        return self.server.post(
            "conversation/search_turns",
            query=query,
            conversations=[self.conversation],
            **body,
        )["turns"]

    def require(self, capability: str) -> None:
        supported = (
            self.portable if capability == "portable" else self.capabilities[capability]
        )
        if not supported:
            pytest.skip(f"base profile: service does not declare {capability}")


def refused(reply: Reply, code: str) -> dict[str, Any]:
    assert reply.status == 400, reply
    validate("Error", reply.body)
    assert reply.body["error"]["code"] == code, reply.body
    details: dict[str, Any] = reply.body["error"]["details"]
    return details


def sent(server: Server, route: str, body: Any) -> tuple[int, bytes]:
    """The status and the answer's body as read, for a bound measured on
    bytes. How the service frames the body (a length or chunks) is its own."""
    request = Request(
        f"{server.url}/v1/{route}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {server.token}"},
        method="POST",
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def too_large(reply: Reply, prefix: str) -> str:
    """A `413 value_error` whose message starts with the words the wire
    pins; the rest of the message is the service's own."""
    assert reply.status == 413, reply
    validate("Error", reply.body)
    assert reply.body["error"]["code"] == "value_error", reply.body
    said = str(reply.body["error"]["message"])
    assert said.startswith(prefix), said
    return said


def conflicted(reply: Reply, conversation: str, reason: str) -> dict[str, Any]:
    """A turn-records conflict: `conversation_id` and `reason` lead the
    details; what follows them is returned."""
    details = refused(reply, "agent_conversation_conflict")
    assert list(details)[:2] == ["conversation_id", "reason"], details
    assert (details["conversation_id"], details["reason"]) == (conversation, reason)
    return {key: details[key] for key in list(details)[2:]}


def require_records(storage: Storage) -> None:
    """Turn records version 1 is optional: absent support is an explicit
    skip under the full and base profiles; the records profile requires it
    before any case runs."""
    capability = storage.capabilities.get("turn_records")
    if capability is None:
        pytest.skip("service does not declare optional turn records version 1")
    assert capability == {"version": 1}


def message(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }
