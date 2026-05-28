"""Production Cosmos-backed :class:`HistoryProvider` factory.

Thin wrapper around :class:`agent_framework_azure_cosmos.CosmosHistoryProvider`
that reads the Cosmos endpoint, database and container names from
:class:`app.settings.Settings` and applies the workflow's persistence
defaults.

Why a wrapper instead of instantiating the MAF class directly at every
call site:

* Centralises the persistence-flag defaults — the backend uses
  ``load_messages=False`` / ``store_inputs=False`` / ``store_outputs=False``
  because the workflow orchestrator persists messages explicitly via
  ``save_messages`` after every turn (see PLAN.md §6.4 decision D11).
  Spreading those flags across the codebase would invite drift.
* Keeps the Cosmos SDK import lazy at module-level so test environments
  that do not have the package available (e.g. minimal CI matrix runs)
  can still import :mod:`app.history` and use the in-memory provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.settings import Settings

if TYPE_CHECKING:
    # ``azure.core.credentials_async.AsyncTokenCredential`` is the formal
    # base for async credentials but importing it eagerly drags in the
    # whole ``azure.core`` runtime. Keep the typing import inside
    # ``TYPE_CHECKING`` so users of the in-memory provider do not pay
    # the cost.
    from agent_framework_azure_cosmos import CosmosHistoryProvider
    from azure.core.credentials_async import AsyncTokenCredential


def build_cosmos_history_provider(
    settings: Settings,
    credential: AsyncTokenCredential,
    **overrides: Any,
) -> CosmosHistoryProvider:
    """Construct the production Cosmos history provider.

    Parameters
    ----------
    settings:
        Backend configuration. ``azure_cosmos_endpoint``,
        ``azure_cosmos_database_name`` and ``azure_cosmos_container_name``
        are read here.
    credential:
        Async token credential (typically
        :class:`azure.identity.aio.DefaultAzureCredential` for managed
        identity). The Cosmos provider authenticates via Entra ID +
        the "Cosmos DB Built-in Data Contributor" RBAC role (ADR-0006).
    **overrides:
        Optional kwargs forwarded to :class:`CosmosHistoryProvider` for
        tests / one-off scripts. Typically empty in production.

    Returns
    -------
    A configured :class:`CosmosHistoryProvider` with the backend's
    persistence defaults (no implicit load/save — the workflow drives
    persistence explicitly).
    """
    # Lazy import: keeps the Cosmos SDK out of the in-memory test path.
    from agent_framework_azure_cosmos import CosmosHistoryProvider

    kwargs: dict[str, Any] = {
        'endpoint': settings.azure_cosmos_endpoint,
        'database_name': settings.azure_cosmos_database_name,
        'container_name': settings.azure_cosmos_container_name,
        'credential': credential,
        'load_messages': False,
        'store_inputs': False,
        'store_outputs': False,
    }
    kwargs.update(overrides)
    return CosmosHistoryProvider(**kwargs)


__all__ = ["build_cosmos_history_provider"]
