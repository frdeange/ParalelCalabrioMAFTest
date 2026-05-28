"""Structural :pep:`544` Protocol for chat-history providers.

We define our own Protocol (rather than re-exporting MAF's abstract
:class:`agent_framework._sessions.HistoryProvider`) for two reasons:

1. The backend only calls a small subset of MAF's surface — ``get_messages``
   and ``save_messages`` plus a few housekeeping methods. A Protocol keeps
   the contract focused on what we actually depend on and lets simple
   in-memory test doubles satisfy it without inheriting MAF's full
   ``ContextProvider`` machinery (``before_run`` / ``after_run`` /
   ``source_id`` / etc.).
2. Protocol + ``runtime_checkable`` gives us cheap ``isinstance`` checks
   in tests without forcing a base-class relationship.

:class:`CosmosHistoryProvider` from ``agent_framework_azure_cosmos``
already satisfies this Protocol structurally — no adapter needed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from agent_framework import Message


@runtime_checkable
class HistoryProvider(Protocol):
    """Minimal contract every history backend must satisfy.

    Method signatures mirror :class:`agent_framework._sessions.HistoryProvider`
    on purpose so production callers can pass the MAF implementation
    unchanged. The Protocol is intentionally *narrower*: it omits the
    workflow-integration hooks (``before_run`` / ``after_run``) the
    backend does not invoke directly.
    """

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        """Return the persisted messages for ``session_id`` (oldest first)."""
        ...

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Persist ``messages`` under ``session_id``."""
        ...


__all__ = ["HistoryProvider"]
