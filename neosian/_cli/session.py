"""Arena session records for the playground.

Single-agent chat persists through `Conversation` + `FileStore` since N2
(see `_cli/chat.py`); arena mode — N models over one in-memory message
list — keeps its own opt-in JSON save at exit.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

ARENA_FORMAT_VERSION: Final = 1  # the `neosian_format` key of the saved JSON


@dataclass
class ArenaModelResponse:
    """A single model's response in arena mode."""

    provider: str
    model: str
    content: str
    tool_calls: list[dict[str, object]]
    elapsed_time: float
    error: str | None = None


@dataclass
class ArenaTurn:
    """A single turn in an arena session."""

    user_message: str
    responses: list[ArenaModelResponse] = field(default_factory=list)


@dataclass
class ArenaSession:
    """Session for arena mode with multiple models.

    Stores turns with responses from each model for comparison.
    """

    agent_name: str = ""
    models: list[dict[str, str]] = field(default_factory=list)
    turns: list[ArenaTurn] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def add_turn(self, user_message: str) -> ArenaTurn:
        """Add a new turn with user message."""
        turn = ArenaTurn(user_message=user_message)
        self.turns.append(turn)
        return turn

    def to_dict(self) -> dict[str, object]:
        """Convert session to a serializable dictionary."""
        return {
            "neosian_format": ARENA_FORMAT_VERSION,
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "mode": "arena",
            "models": self.models,
            "turns": [
                {
                    "user_message": turn.user_message,
                    "responses": [
                        {
                            "provider": r.provider,
                            "model": r.model,
                            "content": r.content,
                            "tool_calls": r.tool_calls,
                            "elapsed_time": r.elapsed_time,
                            "error": r.error,
                        }
                        for r in turn.responses
                    ],
                }
                for turn in self.turns
            ],
        }

    def save(self, path: str | Path) -> Path:
        """Save session to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

        return path

    def generate_filename(self) -> str:
        """Generate a filename for the arena session."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M")
        return f"{timestamp}_{self.agent_name}_arena.json"
