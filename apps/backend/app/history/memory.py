"""In-memory :class:`HistoryProvider` implementation for tests.

This is the test double used by the backend test suite and any local
dev scenario that wants to skip Cosmos entirely. It is **not**
intended for production: there is no persistence, no locking, no TTL,
and the in-memory dict grows unbounded for the lifetime of the process.

Behaviourally it matches what :class:`CosmosHistoryProvider` does for
the methods the backend calls:

* ``get_messages`` returns a snapshot list (oldest first); never
  ``None``.
* ``save_messages`` appends in the order supplied and is idempotent
  per call — repeated calls with the same messages will duplicate them,
  same as the Cosmos provider.
* ``clear`` drops the session entirely; passing ``None`` clears
  *everything* (used in test teardown).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import Message


class InMemoryHistoryProvider:
    """Dict-backed history provider for tests.

    Structurally satisfies :class:`app.history.HistoryProvider`.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, list[Message]] = {}

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        if session_id is None:
            return []
        # Defensive copy: callers may mutate the returned list (e.g. the
        # workflow appends user/assistant turns before re-saving).
        return list(self._sessions.get(session_id, ()))

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if session_id is None:
            # Match Cosmos provider semantics: silently drop messages with
            # no session key. The backend always supplies one in practice.
            return
        self._sessions.setdefault(session_id, []).extend(messages)

    async def clear(self, session_id: str | None) -> None:
        if session_id is None:
            self._sessions.clear()
            return
        self._sessions.pop(session_id, None)

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def close(self) -> None:
        # Nothing to release; provided for parity with CosmosHistoryProvider.
        return None


__all__ = ["InMemoryHistoryProvider"]
