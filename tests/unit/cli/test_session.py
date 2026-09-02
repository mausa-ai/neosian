"""Arena session records: aware clocks and a versioned JSON shape (EC-6)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neosian._cli.session import ARENA_FORMAT_VERSION, ArenaModelResponse, ArenaSession


def _session() -> ArenaSession:
    session = ArenaSession(agent_name="probe", models=[{"provider": "fake"}])
    turn = session.add_turn("hello")
    turn.responses.append(
        ArenaModelResponse(
            provider="fake", model="fake", content="hi", tool_calls=[], elapsed_time=0.1
        )
    )
    return session


@pytest.mark.unit
class TestArenaSession:
    def test_started_at_is_aware_utc(self) -> None:
        started = datetime.fromisoformat(ArenaSession().started_at)
        assert started.tzinfo is not None
        assert started.utcoffset() == datetime.now(UTC).utcoffset()

    def test_dict_carries_format_version(self) -> None:
        data = _session().to_dict()
        assert data["neosian_format"] == ARENA_FORMAT_VERSION == 1
        assert data["mode"] == "arena"

    def test_save_round_trips(self, tmp_path: Path) -> None:
        session = _session()
        path = session.save(tmp_path / "nested" / session.generate_filename())
        assert path.name.endswith("_probe_arena.json")
        assert json.loads(path.read_text(encoding="utf-8")) == session.to_dict()
