"""Selects the right :class:`HistoryProvider` implementation.

The factory keeps the wiring decision in one place so callers (lifespan
hook, tests, dev REPL) do not branch on credentials themselves. Rule:

* If ``credential`` is provided **and** ``in_memory`` is not set, return
  a real :class:`CosmosHistoryProvider` from
  :mod:`app.history.cosmos`.
* Otherwise return an :class:`InMemoryHistoryProvider`. Tests pass
  ``in_memory=True`` explicitly so the behaviour is obvious.

When ``in_memory`` is left unset and no credential is supplied the
factory still returns the in-memory provider — convenient for local
smoke tests and the unit-test default — and emits a single info-level
log line so production misconfiguration (forgetting the credential) is
visible in App Insights.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.history.memory import InMemoryHistoryProvider
from app.history.protocol import HistoryProvider
from app.settings import Settings

if TYPE_CHECKING:
    from azure.core.credentials_async import AsyncTokenCredential

logger = logging.getLogger(__name__)


def get_history_provider(
    settings: Settings,
    *,
    credential: AsyncTokenCredential | None = None,
    in_memory: bool | None = None,
    **cosmos_overrides: Any,
) -> HistoryProvider:
    """Return the appropriate history provider for the current context.

    Parameters
    ----------
    settings:
        Backend configuration singleton.
    credential:
        Async token credential. When ``None`` (and ``in_memory`` is not
        explicitly ``False``) the factory falls back to the in-memory
        provider — useful for unit tests and the dev REPL.
    in_memory:
        Force the in-memory provider (``True``) or the Cosmos provider
        (``False``). When ``None`` (default), the choice is derived from
        whether ``credential`` was supplied.
    **cosmos_overrides:
        Forwarded to :func:`build_cosmos_history_provider`. Useful in
        tests that need a custom Cosmos endpoint.
    """
    if in_memory is True:
        return InMemoryHistoryProvider()

    if in_memory is False or credential is not None:
        # Lazy import keeps the Cosmos SDK out of the in-memory path.
        from app.history.cosmos import build_cosmos_history_provider

        if credential is None:
            # Misconfiguration: caller explicitly asked for Cosmos but
            # forgot the credential. Surface it loudly rather than
            # silently degrading to the in-memory provider.
            raise ValueError(
                "in_memory=False requires an async credential for Cosmos"
            )
        return build_cosmos_history_provider(
            settings, credential, **cosmos_overrides
        )

    logger.info(
        "history.factory: no credential supplied, falling back to in-memory "
        "provider (set credential= for production)"
    )
    return InMemoryHistoryProvider()


__all__ = ["get_history_provider"]
