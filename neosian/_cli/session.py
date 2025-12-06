"""Session management for playground.

Handles in-memory conversation history and optional persistence.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from neosian._foundation.llm.base import Message, Role


@dataclass
class Session:
    """In-memory conversation session.

    Stores messages during a playground session and can save to JSON.
    """

    messages: list[Message] = field(default_factory=list)
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
                    "content": m.content,
                    "tool_calls": [asdict(tc) for tc in m.tool_calls],
                    "tool_call_id": m.tool_call_id,
                }
                for m in self.messages
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
