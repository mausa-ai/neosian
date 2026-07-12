"""Session management for playground.

Handles in-memory conversation history and optional persistence.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from neosian._foundation.llm.base import Message, Role, content_to_json
from neosian._foundation.shared.types import GuardrailResult


@dataclass
class BlockedMessage:
    """A message that was blocked by guardrails."""

    content: str
    timestamp: str
    guardrail_result: GuardrailResult


@dataclass
class Session:
    """In-memory conversation session.

    Stores messages during a playground session and can save to JSON.
    Blocked messages are stored separately for logging but excluded from LLM history.
    """

    messages: list[Message] = field(default_factory=list)
    blocked_messages: list[BlockedMessage] = field(default_factory=list)
    agent_name: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_message(self, message: Message) -> None:
        """Add a message to the session."""
        self.messages.append(message)

    def add_user_message(self, content: str) -> None:
        """Add a user message to the session."""
        self.messages.append(Message(role=Role.USER, content=content))

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the session."""
        self.messages.append(Message(role=Role.ASSISTANT, content=content))

    def add_blocked_message(
        self, content: str, guardrail_result: GuardrailResult
    ) -> None:
        """Add a blocked message to the session.

        Blocked messages are stored separately and not included in LLM history.
        """
        self.blocked_messages.append(
            BlockedMessage(
                content=content,
                timestamp=datetime.now().isoformat(),
                guardrail_result=guardrail_result,
            )
        )

    def get_messages(self) -> list[Message]:
        """Get all messages (excluding system)."""
        return [m for m in self.messages if m.role != Role.SYSTEM]

    def to_dict(self) -> dict[str, object]:
        """Convert session to a serializable dictionary."""
        return {
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "messages": [
                {
                    "role": m.role.value,
                    "content": content_to_json(m.content),
                    "tool_calls": [asdict(tc) for tc in m.tool_calls],
                    "tool_call_id": m.tool_call_id,
                }
                for m in self.messages
            ],
            "blocked_messages": [
                {
                    "content": bm.content,
                    "timestamp": bm.timestamp,
                    "guardrail_result": asdict(bm.guardrail_result),
                }
                for bm in self.blocked_messages
            ],
        }

    def save(self, path: str | Path) -> Path:
        """Save session to a JSON file.

        Args:
            path: Path to save the session.

        Returns:
            The path where the session was saved.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

        return path

    def generate_filename(self) -> str:
        """Generate a filename for the session.

        Returns:
            A filename like '2024-12-06_14-32_my-agent.json'
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return f"{timestamp}_{self.agent_name}.json"


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
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_turn(self, user_message: str) -> ArenaTurn:
        """Add a new turn with user message."""
        turn = ArenaTurn(user_message=user_message)
        self.turns.append(turn)
        return turn

    def to_dict(self) -> dict[str, object]:
        """Convert session to a serializable dictionary."""
        return {
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
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return f"{timestamp}_{self.agent_name}_arena.json"
