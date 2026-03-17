"""Abstract base for blackboard providers.

Defines the interface that all blackboard implementations must follow.
The app implements this to provide dynamic context to agents.
"""

from abc import ABC, abstractmethod

from neosian._foundation.shared.types import BlackboardEntry


class BlackboardProvider(ABC):
    """Abstract base for blackboard providers.

    Blackboard provides dynamic, mutable context that the app maintains
    and the agent reads/updates on demand. Entries are predefined —
    the agent can read and update, but not create or delete entries.

    Implementations:
        - FileBlackboard: reads/writes .md files from a directory (built-in)
        - Custom: DynamoDB, Redis, PostgreSQL, in-memory, etc.

    Example:
        class MyBlackboard(BlackboardProvider):
            async def list_entries(self) -> list[BlackboardEntry]:
                return [BlackboardEntry(name="workspace", description="...")]

            async def read_entry(self, name: str) -> str | None:
                return self._store.get(name)

            async def update_entry(self, name: str, content: str) -> bool:
                if name not in self._store:
                    return False
                self._store[name] = content
                return True
    """

    @abstractmethod
    async def list_entries(self) -> list[BlackboardEntry]:
        """List all available blackboard entries with names and descriptions."""
        ...

    @abstractmethod
    async def read_entry(self, name: str) -> str | None:
        """Read the current content of a blackboard entry.

        Args:
            name: The entry name to read.

        Returns:
            The entry content, or None if the entry does not exist.
        """
        ...

    @abstractmethod
    async def update_entry(self, name: str, content: str) -> bool:
        """Update an existing blackboard entry.

        Cannot create new entries — only update existing ones.

        Args:
            name: The entry name to update.
            content: The new content.

        Returns:
            True if updated, False if entry does not exist.
        """
        ...
