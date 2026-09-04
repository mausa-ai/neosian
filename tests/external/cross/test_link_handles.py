"""Link handles measured on a real model (DESIGN §23, NJ).

The keyless tier pins the mechanism — every link crosses the boundary
whole, a `[link N]` in tool arguments reaches the tool expanded. This
row asks the one question a fake cannot answer: given the compacted
log and the footer's convention, does the model reuse a link by its
handle? One cell per provider row, the same rows as the memory
baselines; the tool's receipt is the assertion (the URL it was handed),
the handle's use is printed and transcribed — measured, never asserted.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from neosian import AgentConfig, AnyModel, Model, Tool, ToolResult
from neosian._foundation.shared.types import SystemPrompt
from neosian.conversation import CompactionConfig, Conversation, FileStore
from tests.external.lanes import LANES, Lane
from tests.external.pacing import Pacer, door_client

_PROVIDER_CASES = [
    pytest.param(Model.GPT_5_1, "openai_api_key", "OPENAI_API_KEY", id="openai"),
    pytest.param(
        Model.CLAUDE_SONNET_5, "anthropic_api_key", "ANTHROPIC_API_KEY", id="anthropic"
    ),
    pytest.param(
        Model.CEREBRAS_GPT_OSS_120B,
        "cerebras_api_key",
        "CEREBRAS_API_KEY",
        id="cerebras",
    ),
]


async def _measure(
    model: AnyModel, key: str, env_name: str, lane: Lane | None, root: Path
) -> None:
    fetched: list[str] = []

    @Tool(name="fetch", description="Fetch a document by URL and return its text.")
    async def fetch(url: str) -> ToolResult[str]:
        fetched.append(url)
        return ToolResult.ok("Widget design v2: the widget ships in blue.")

    url = f"https://docs.example.test/specs/{uuid4()}/widget-design-v2"
    pacer = None if lane is None else Pacer.of(lane)
    config = AgentConfig(
        system_prompt=SystemPrompt(
            "You are a careful assistant. Use the fetch tool to open a "
            "document whenever you are asked what it says."
        ),
        model=model,
        tools=[fetch],
        enable_todo=False,
        client_factory=(
            None if lane is None else (lambda _p: door_client(lane, key, pacer))
        ),
    )
    with patch.dict(os.environ, {env_name: key}, clear=True):
        async with Conversation(
            config,
            store=FileStore(root),
            conversation_id="links",
            compaction=CompactionConfig(hot_turns=1),
        ) as convo:
            await convo.send(
                f"The widget design document is at {url} — just acknowledge, "
                "do not open it yet."
            )
            await convo.send("Unrelated: what is 2 + 2? One word.")
            entries = (await convo.compact()).entries
            assert entries and "[link 1]" in entries[0].text
            assert url not in entries[0].text
            response = await convo.send(
                "Now open the widget design document from the log and tell "
                "me what colour the widget ships in."
            )
    assert fetched == [url]  # the tool was handed the original — the recall claim
    used_handle = any(
        "[link 1]" in json.dumps(call.arguments) for call in response.tool_calls_made
    )
    print(
        f"cell {model.value}: handle_used={used_handle} "
        f"log_chars={len(entries[0].text)} answer={response.message.content!r}"
    )


class TestLinkHandles:
    @pytest.mark.parametrize(("model", "fixture", "env_name"), _PROVIDER_CASES)
    async def test_a_link_is_reused_by_handle_after_compaction(
        self,
        model: Model,
        fixture: str,
        env_name: str,
        request: pytest.FixtureRequest,
        tmp_path: Path,
    ) -> None:
        key = request.getfixturevalue(fixture)  # skips when the env var is unset
        await _measure(model, key, env_name, None, tmp_path / "store")

    @pytest.mark.parametrize("lane", LANES, ids=lambda lane: lane.name)
    async def test_a_door_reuses_a_link_by_handle(
        self, lane: Lane, request: pytest.FixtureRequest, tmp_path: Path
    ) -> None:
        key = request.getfixturevalue(lane.key_fixture)  # skips when unset
        await _measure(
            lane.registered(), key, lane.door.api_key_env, lane, tmp_path / "store"
        )
