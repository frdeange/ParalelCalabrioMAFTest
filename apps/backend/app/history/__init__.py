"""History provider abstraction.

The backend stores chat history in Cosmos DB via
``agent_framework_azure_cosmos.CosmosHistoryProvider`` in production but
needs a swappable in-memory implementation for tests so the suite can
run without a Cosmos emulator (issue #10, ADR-0006).

The :class:`HistoryProvider` :pep:`544` Protocol below captures *only*
the surface the backend actually calls — ``get_messages`` and
``save_messages``, optionally ``clear`` / ``close`` / ``list_sessions``.
Both production and test implementations are structurally compatible:

* :class:`CosmosHistoryProvider` from the official MAF integration
  satisfies the Protocol via duck typing (we re-export a builder from
  :mod:`app.history.cosmos`).
* :class:`InMemoryHistoryProvider` (:mod:`app.history.memory`) is a
  trivial dict-backed implementation used by tests.

The :func:`get_history_provider` factory picks the right implementation
based on whether a credential is supplied.
"""

from __future__ import annotations

from app.history.factory import get_history_provider
from app.history.memory import InMemoryHistoryProvider
from app.history.protocol import HistoryProvider

__all__ = [
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "get_history_provider",
]
