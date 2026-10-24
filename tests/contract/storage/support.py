"""A unique test namespace and explicit wire calls, not an SDK implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def message(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": text,
        "reasoning": None,
        "tool_calls": [],
        "tool_call_id": None,
    }
