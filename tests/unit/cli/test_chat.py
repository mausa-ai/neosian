"""The conversation-backed chat helpers and the session chat and
playground share (N2 slice C; DESIGN §14.6, §35). Zero keys."""

import io
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest
from rich.console import Console
from textual.pilot import Pilot
from textual.widgets import Static

import neosian._cli.chat as chat_module
from neosian import AgentConfig, GuardrailMode, GuardrailsConfig, Model, PolicyBuilder
from neosian._cli.chat import (
    chat_config,
    describe_memory,
    new_conversation_id,
    open_chat,
    opening,
    resolve_resume,
    run_chat,
    streams,
)
from neosian._foundation.conversation.ids import parse_conversation_id
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.shared.exceptions import ConversationIdInvalidError
from tests.unit.cli.piloting import piloted, plain, send

_SYSTEM = "You are a test agent."
_NOW = datetime(2026, 8, 20, 14, 32, 7)


def _config(**kwargs: object) -> AgentConfig:
    fake = FakeClient(FakeScript(turns=(FakeTurn(content="ok"),)))
    return AgentConfig(
        system_prompt=_SYSTEM,
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestNewConversationId:
    def test_embeds_stamp_and_agent_name(self) -> None:
        assert new_conversation_id("my_agent", now=_NOW) == "20260820-143207-my_agent"

    @pytest.mark.parametrize(
        "hostile",
        ["my agent!", "../etc", "a" * 200, "", "café/λ", ".."],
    )
    def test_hostile_names_still_yield_valid_ids(self, hostile: str) -> None:
        result = new_conversation_id(hostile, now=_NOW)
        assert str(parse_conversation_id(result)) == result
        assert result.startswith("20260820-143207")
        assert len(result) <= 128

    def test_default_clock_is_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The fallback clock asks for UTC, so ids sort across zones (EC-7)
        — pinned on the call, not on the runner's zone."""
        asked: list[tzinfo | None] = []

        class _Clock(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> Self:
                asked.append(tz)
                return super().now(tz)

        monkeypatch.setattr("neosian._cli.chat.datetime", _Clock)
        result = new_conversation_id("a")
        assert asked == [UTC]
        assert len(result[:15]) == 15

    def test_two_clocks_give_two_ids(self) -> None:
        later = datetime(2026, 8, 20, 14, 32, 8)
        assert new_conversation_id("a", now=_NOW) != new_conversation_id("a", now=later)


@pytest.mark.unit
class TestResolveResume:
    def test_plain_id_passes_through(self) -> None:
        assert resolve_resume("20260820-143207-my_agent") == "20260820-143207-my_agent"

    @pytest.mark.parametrize(
        "path_like",
        [
            "last.json",  # matches the id grammar — the trap the check exists for
            ".neosian/sessions/last.json",
            "/abs/path/session.json",
            "C:\\sessions\\last.json",
        ],
    )
    def test_legacy_path_forms_are_refused(self, path_like: str) -> None:
        with pytest.raises(ValueError, match="--resume"):
            resolve_resume(path_like)

    def test_invalid_id_raises_the_grammar_error(self) -> None:
        with pytest.raises(ConversationIdInvalidError):
            resolve_resume("bad id!")


@pytest.mark.unit
class TestOpenChat:
    async def test_send_lands_in_the_home_store_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The home fixture points NEOSIAN_HOME at tmp_path/home (DESIGN §22).
        monkeypatch.chdir(tmp_path)
        convo = open_chat(_config(), conversation_id="t1")
        await convo.send("hi")
        home = tmp_path / "home"
        assert (home / "conversations" / "t1" / "turns.jsonl").exists()

    def test_a_config_without_memory_gets_the_project_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "demo proj"
        project.mkdir()
        monkeypatch.chdir(project)
        store = FileStore(tmp_path / "home")
        config = _config()
        derived = chat_config(config, store)
        assert config.memory is None  # never mutated in place
        assert derived.memory is not None
        assert derived.memory.store is store
        assert [m.mount_path for m in derived.memory.mounts] == ["user", "project"]
        assert derived.memory.mounts[1].scope.endswith("/proj:demo-proj")
        assert describe_memory(config).endswith("/proj:demo-proj")

    def test_a_config_with_memory_is_left_alone(self, tmp_path: Path) -> None:
        memory = MemoryConfig(
            store=FileStore(tmp_path / "memstore"),
            mounts=(Mount(scope="user:demo", mount_path="memories"),),
        )
        config = _config(memory=memory)
        assert chat_config(config, FileStore(tmp_path / "home")) is config
        assert describe_memory(config) == "/memories = user:demo"

    async def test_resume_by_id_round_trips(self) -> None:
        first = open_chat(_config(), conversation_id="t1")
        await first.send("hi")
        second = open_chat(_config(), conversation_id="t1")
        await second.start()
        assert [m.content for m in second.messages] == ["hi", "ok"]

    async def test_callers_config_is_never_mutated(self, tmp_path: Path) -> None:
        """The old `_run_chat` appended the memory section to the caller's
        `system_prompt` in place; Conversation derives its own config."""
        memory = MemoryConfig(
            store=FileStore(tmp_path / "memstore"),
            mounts=(Mount(scope="user:demo", mount_path="memories"),),
        )
        config = _config(memory=memory)
        convo = open_chat(config, conversation_id="t1")
        await convo.send("hi")
        assert config.system_prompt == _SYSTEM
        assert config.memory is memory


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=120, no_color=True), out


@pytest.mark.unit
class TestTheSession:
    """`run_chat` opens the Textual session; a pilot drives it here."""

    @pytest.fixture(autouse=True)
    def _project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

    async def test_a_turn_persists_and_the_resume_line_stays_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        async def script(pilot: Pilot[None]) -> None:
            seen.append(plain(pilot.app.query_one("#opening", Static).content))
            await send(pilot, "hi")
            await pilot.press("ctrl+d")

        piloted(monkeypatch, script)
        console, out = _console()
        await run_chat(console, _config(), "probe", conversation_id="s1", resumed=False)
        assert "Agent: probe" in seen[0] and "s1" in seen[0]
        assert out.getvalue().strip() == "Resume: --resume s1"
        assert len(await FileStore(tmp_path / "home").read_turns("s1")) == 1

    async def test_leaving_at_once_stores_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def script(pilot: Pilot[None]) -> None:
            await pilot.press("ctrl+d")

        piloted(monkeypatch, script)
        console, _ = _console()
        await run_chat(console, _config(), "probe", conversation_id="s1", resumed=False)
        assert await FileStore(tmp_path / "home").read_turns("s1") == ()

    async def test_a_resumed_id_names_what_it_found(self) -> None:
        config = _config()
        fresh = await open_chat(config, conversation_id="s2").start()
        assert (
            opening(config, "p", fresh, resumed=True).notice
            == "No turns stored under s2 yet — starting fresh."
        )
        assert opening(config, "p", fresh, resumed=False).notice is None
        await open_chat(config, conversation_id="s3").send("hi")
        found = await open_chat(config, conversation_id="s3").start()
        assert (
            opening(config, "p", found, resumed=True).notice
            == "Resumed 2 messages from s3"
        )

    async def test_a_conversation_that_cannot_start_exits_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(_config: AgentConfig, **_: Any) -> None:
            raise RuntimeError("no home")

        monkeypatch.setattr(chat_module, "open_chat", broken)
        console, out = _console()
        with pytest.raises(SystemExit) as excinfo:
            await run_chat(console, _config(), "p", conversation_id="s", resumed=False)
        assert excinfo.value.code == 1
        assert "Error starting conversation: no home" in out.getvalue()


@pytest.mark.unit
class TestStreams:
    def test_plain_and_input_guarded_configs_stream(self) -> None:
        assert streams(_config()) is True
        guarded = _config(
            guardrails=GuardrailsConfig(
                input_mode=GuardrailMode.POLICY_ONLY, input_policy=PolicyBuilder.test()
            )
        )
        assert streams(guarded) is True

    def test_output_guardrails_take_the_blocking_path(self) -> None:
        guarded = _config(
            guardrails=GuardrailsConfig(
                output_mode=GuardrailMode.POLICY_ONLY,
                output_policy=PolicyBuilder.test(),
            )
        )
        assert streams(guarded) is False
