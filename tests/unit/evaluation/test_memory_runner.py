"""One memory scenario, one cell — sessions as bare derived Agents
(DESIGN §13.12)."""

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian._foundation.evaluation.expectations import parse_expectation
from neosian._foundation.evaluation.memory_runner import run_scenario
from neosian._foundation.evaluation.memory_types import (
    DocumentExpectation,
    MemoryScenario,
    MemorySession,
    SeedDocument,
    StoreExpectation,
    Transport,
)
from neosian._foundation.evaluation.types import EvalTurn
from neosian._foundation.llm.base import Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import EvalRunError
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    Provider,
    SystemPrompt,
    ToolCallId,
    ToolName,
)

_MOUNT = Mount(scope="user:eval", mount_path="user", description="user facts")


def _base(**kwargs: object) -> AgentConfig:
    return AgentConfig(
        system_prompt=SystemPrompt("agent under test"),
        model=Model.FAKE,
        enable_todo=False,
        **kwargs,  # type: ignore[arg-type]
    )


def _call(arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c1"), name=ToolName("memory"), arguments=arguments),
        )
    )


def _turn(user: str, expect: dict[str, object] | None = None) -> EvalTurn:
    return EvalTurn(
        user=user,
        expect=parse_expectation(expect or {"tool": "memory"}, "test", "expect"),
    )


_CREATE = _call(
    {"command": "create", "path": "/user/preferences", "content": "Espresso only."}
)
_VIEW = _call({"command": "view", "path": "/user/preferences"})


def _two_session_scenario() -> MemoryScenario:
    return MemoryScenario(
        name="recall",
        sessions=(
            MemorySession(
                name="record",
                turns=(_turn("Remember: espresso only."),),
                script=(_CREATE, FakeTurn(content="Noted.")),
                expect_store=StoreExpectation(
                    documents=(DocumentExpectation(path="/user/preferences"),)
                ),
            ),
            MemorySession(
                name="recall",
                turns=(_turn("What do I drink?"),),
                script=(_VIEW, FakeTurn(content="Espresso.")),
                expect_store=StoreExpectation(counts={"/user": 1}),
            ),
        ),
    )


@pytest.mark.unit
class TestSessions:
    async def test_two_scripted_sessions_share_one_store(self, tmp_path: Path) -> None:
        """Each session gets its own FakeClient (fresh cursor); the store
        persists across them; the memory capture is executed=True."""
        result = await run_scenario(
            _base(),
            Transport.FUNCTION,
            Model.FAKE,
            _two_session_scenario(),
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        assert result.variant == "function"
        captures = [c for t in result.turns for c in t.tool_calls]
        assert [str(c.name) for c in captures] == ["memory", "memory"]
        assert all(c.executed and c.ok for c in captures)

    async def test_session_two_prompt_carries_the_regenerated_index(
        self, tmp_path: Path
    ) -> None:
        """The frozen-index rule: session 1's write surfaces in session
        2's system prompt — observed through a caller factory, the
        scriptless path the external baselines ride."""
        clients: list[FakeClient] = []
        scripts = [
            FakeScript(turns=(_CREATE, FakeTurn(content="Noted."))),
            FakeScript(turns=(_VIEW, FakeTurn(content="Espresso."))),
        ]

        def factory(_provider: Provider) -> FakeClient:
            fake = FakeClient(scripts[len(clients)])
            clients.append(fake)
            return fake

        scenario = MemoryScenario(
            name="recall",
            sessions=(
                MemorySession(name="record", turns=(_turn("Remember."),)),
                MemorySession(name="recall", turns=(_turn("What?"),)),
            ),
        )
        result = await run_scenario(
            _base(client_factory=factory),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert result.passed
        assert len(clients) == 2
        first = clients[0].calls[0].messages[0]
        second = clients[1].calls[0].messages[0]
        assert first.role is Role.SYSTEM and second.role is Role.SYSTEM
        assert "- /user/preferences" not in str(first.content)
        assert "- /user/preferences" in str(second.content)

    @pytest.mark.parametrize("transport", [Transport.FUNCTION, Transport.CLI])
    async def test_version_rows_carry_the_session_actor(
        self, tmp_path: Path, transport: Transport
    ) -> None:
        """The eval actor survives both transports — on cli it rides
        `--actor` verbatim through the argv boundary."""
        root = tmp_path / "store"
        await run_scenario(
            _base(),
            transport,
            Model.FAKE,
            _two_session_scenario(),
            mounts=(_MOUNT,),
            store_root=root,
        )
        rows = await FileStore(root).versions(_MOUNT.scope, "preferences")
        assert [row.actor for row in rows] == ["eval:recall:record"]


@pytest.mark.unit
class TestSeedAndMaintain:
    async def test_seeds_exist_before_session_one_backdated_and_audited(
        self, tmp_path: Path
    ) -> None:
        """Seeded documents are in session 1's frozen index, carry
        `actor: eval:seed`, and their timestamps sit `age_days` back."""
        root = tmp_path / "store"
        scenario = MemoryScenario(
            name="seeded",
            seed=(
                SeedDocument(path="/user/coffee", content="Espresso only."),
                SeedDocument(path="/user/fresh", content="New note.", age_days=0),
            ),
            sessions=(
                MemorySession(
                    name="look",
                    turns=(_turn("What do I drink?"),),
                    script=(_VIEW, FakeTurn(content="Espresso.")),
                    expect_store=StoreExpectation(counts={"/user": 2}),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=root,
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        store = FileStore(root)
        rows = await store.versions(_MOUNT.scope, "coffee")
        assert [row.actor for row in rows] == ["eval:seed"]
        coffee = await store.read(_MOUNT.scope, "coffee")
        fresh = await store.read(_MOUNT.scope, "fresh")
        assert coffee is not None and fresh is not None
        now = datetime.now(UTC)
        assert coffee.updated_at < now - timedelta(days=29)
        assert fresh.updated_at > now - timedelta(days=1)

    async def test_maintain_runs_both_stages_under_the_session_actor(
        self, tmp_path: Path
    ) -> None:
        """A turn-less maintain session: the deterministic stage merges
        the byte-identical seeds (keeper = first path on the created_at
        tie), the scripted batch's delete lands, and every mutation is
        audited under the session actor."""
        root = tmp_path / "store"
        scenario = MemoryScenario(
            name="garden",
            seed=(
                SeedDocument(path="/user/coffee", content="Espresso only."),
                SeedDocument(path="/user/coffee-copy", content="Espresso only."),
                SeedDocument(path="/user/stale", content="Uses the old API."),
            ),
            sessions=(
                MemorySession(
                    name="pass",
                    turns=(),
                    script=(
                        FakeTurn(
                            content='{"ops": [{"command": "delete", '
                            '"path": "/user/stale"}]}'
                        ),
                    ),
                    maintain=True,
                    expect_store=StoreExpectation(
                        counts={"/user": 1},
                        absent=("/user/coffee-copy", "/user/stale"),
                    ),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=root,
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        assert len(result.turns) == 1  # the synthetic maintenance step
        store = FileStore(root)
        assert await store.read(_MOUNT.scope, "coffee") is not None
        copy_rows = await store.versions(_MOUNT.scope, "coffee-copy")
        assert copy_rows[0].action == "deleted"
        assert copy_rows[0].actor == "eval:garden:pass"
        stale_rows = await store.versions(_MOUNT.scope, "stale")
        assert stale_rows[0].action == "deleted"
        assert stale_rows[0].actor == "eval:garden:pass"


@pytest.mark.unit
class TestDerivation:
    async def test_caller_config_is_untouched(self, tmp_path: Path) -> None:
        base = _base()
        tools_before = base.tools
        snapshot = copy.copy(base)
        await run_scenario(
            base,
            Transport.NATIVE,
            Model.FAKE,
            _two_session_scenario(),
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert base.tools is tools_before and base.tools == []
        assert base.native_memory is False
        assert base == snapshot

    async def test_native_transport_marks_the_tool_before_the_call(
        self, tmp_path: Path
    ) -> None:
        """The wire-visible half of the transport axis: the memory
        ToolDefinition reaches the client with (or without) the marker —
        and the tuple unpack pins that exactly ONE `memory` tool is on
        the wire (a cli cell must not also register the function tool)."""
        for transport, expected in (
            (Transport.NATIVE, "memory_20250818"),
            (Transport.FUNCTION, None),
            (Transport.CLI, None),
        ):
            clients: list[FakeClient] = []

            def factory(
                _provider: Provider, clients: list[FakeClient] = clients
            ) -> FakeClient:
                fake = FakeClient(
                    FakeScript(turns=(FakeTurn(content="ok"),), repeat_last=True)
                )
                clients.append(fake)
                return fake

            scenario = MemoryScenario(
                name="probe",
                sessions=(
                    MemorySession(name="one", turns=(_turn("hi", {"no_tool": True}),)),
                ),
            )
            result = await run_scenario(
                _base(client_factory=factory),
                transport,
                Model.FAKE,
                scenario,
                mounts=(_MOUNT,),
                store_root=tmp_path / f"store-{transport.value}",
            )
            assert result.passed
            (memory_tool,) = [
                t for t in clients[0].calls[0].tools if t.name == "memory"
            ]
            assert memory_tool.native_type == expected

    async def test_memory_bearing_base_is_refused(self, tmp_path: Path) -> None:
        store = FileStore(tmp_path / "own-store")
        base = _base(memory=MemoryConfig(store=store, mounts=(_MOUNT,)))
        with pytest.raises(EvalRunError, match="mounts:"):
            await run_scenario(
                base,
                Transport.FUNCTION,
                Model.FAKE,
                _two_session_scenario(),
                mounts=(_MOUNT,),
                store_root=tmp_path / "store",
            )


@pytest.mark.unit
class TestFailures:
    async def test_store_miss_folds_into_the_last_turn_with_the_root(
        self, tmp_path: Path
    ) -> None:
        scenario = MemoryScenario(
            name="dedup",
            sessions=(
                MemorySession(
                    name="only",
                    turns=(_turn("hi"),),
                    script=(_CREATE, FakeTurn(content="Noted.")),
                    expect_store=StoreExpectation(counts={"/user": 2}),
                ),
            ),
        )
        root = tmp_path / "store"
        result = await run_scenario(
            _base(),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=root,
        )
        assert not result.passed
        failures = result.turns[-1].failures
        assert any("expected 2 document(s) under /user" in f for f in failures)
        assert f"store root: {root}" in failures

    async def test_stop_on_failure_skips_later_sessions(self, tmp_path: Path) -> None:
        scenario = MemoryScenario(
            name="stops",
            sessions=(
                MemorySession(
                    name="fails",
                    turns=(_turn("hi", {"no_tool": True}),),
                    script=(_CREATE, FakeTurn(content="Noted.")),
                ),
                MemorySession(
                    name="never-runs",
                    turns=(_turn("hi"),),
                    script=(_VIEW, FakeTurn(content="x")),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.FUNCTION,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert not result.passed
        assert len(result.turns) == 1
        assert result.turns[0].failures[0].startswith("session 'fails': ")
