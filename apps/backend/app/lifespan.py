"""FastAPI lifespan: build the workflow and wire the AG-UI endpoint.

The lifespan opens every async resource the chat workflow needs
(Azure credential, MCP tool sessions, Foundry chat client, Cosmos
history provider), assembles the workflow once, and registers the
AG-UI endpoint against the now-built workflow. Everything is torn
down on shutdown via an :class:`contextlib.AsyncExitStack`.

Why this lives in its own module
--------------------------------
* Keeps :mod:`app.main` focused on the *static* API surface
  (``/healthz`` plus any future router) so it can be imported in
  tests without paying the cost of Azure auth.
* Makes the bootstrap testable in isolation: the test suite swaps in
  a fake lifespan via :func:`app.main.create_app(lifespan=...)` and
  injects mock workflows / providers.

Design notes
------------
The AG-UI helper (:func:`agent_framework_ag_ui.add_agent_framework_fastapi_endpoint`)
captures the workflow in a closure at registration time. We cannot
register the endpoint at module-import time because the workflow
needs the open MCP sessions. Registering it *inside* the lifespan
startup is supported by FastAPI/Starlette — the router accepts new
routes until the first request hits it, which by definition happens
after the lifespan startup completes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework.observability import create_resource, enable_instrumentation
from agent_framework_ag_ui import add_agent_framework_fastapi_endpoint
from azure.identity.aio import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor

from app.history import get_history_provider
from app.mcp import build_mcp_tool
from app.settings import Settings, get_settings
from app.workflow import build_workflow

if TYPE_CHECKING:
    from fastapi import FastAPI


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


def _init_telemetry(settings: Settings) -> None:
    """Initialise Azure Monitor + MAF instrumentation idempotently.

    Calling :func:`configure_azure_monitor` twice raises, so the
    function is guarded by ``settings.enable_instrumentation`` and is
    only invoked from the lifespan startup (which itself runs once per
    process). Sensitive-data emission is controlled by
    ``ENABLE_SENSITIVE_DATA``; the default ``True`` matches the dev
    REPL — production deployments should set it to ``false`` (PLAN.md §11).
    """
    if not settings.enable_instrumentation:
        logger.info(
            "telemetry: instrumentation disabled via ENABLE_INSTRUMENTATION=false"
        )
        return

    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        resource=create_resource(service_name=settings.otel_service_name),
        enable_live_metrics=True,
    )
    enable_instrumentation(enable_sensitive_data=settings.enable_sensitive_data)
    logger.info(
        "telemetry: configured for service=%s sensitive_data=%s",
        settings.otel_service_name,
        settings.enable_sensitive_data,
    )


# --------------------------------------------------------------------------
# Lifespan
# --------------------------------------------------------------------------


# Path the AG-UI endpoint is mounted at. Lives at module scope so tests
# can import the constant without re-running the lifespan.
AGUI_PATH = "/agui"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Production lifespan: full Azure wiring.

    Steps (in order):

    1. Initialise telemetry — every span emitted after this point is
       attached to the App Insights resource.
    2. Open ``DefaultAzureCredential`` for the lifetime of the app.
    3. Open the two MCP tool sessions (schema discovery + executor).
    4. Build the Foundry chat client and the three workflow agents.
    5. Wire the Cosmos history provider.
    6. Assemble the workflow and register ``POST /agui``.
    7. Yield to FastAPI; on shutdown the ``AsyncExitStack`` rolls back
       every async context.

    The credential and the MCP sessions stay alive across every
    request because they are entered into the stack — closing them
    early would break the very first ``/agui`` call.
    """
    settings = get_settings()
    _init_telemetry(settings)

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())

        mcp_schema = await stack.enter_async_context(
            build_mcp_tool(
                settings,
                allowed_tools=["listTables", "getSchema"],
            )
        )
        mcp_exec = await stack.enter_async_context(
            build_mcp_tool(
                settings,
                allowed_tools=["executeQuery"],
            )
        )

        chat_client = FoundryChatClient(
            project_endpoint=settings.foundry_project_endpoint,
            model=settings.foundry_deployment_name,
            credential=credential,
        )

        history_provider = get_history_provider(
            settings, credential=credential
        )

        intent_agent = Agent(
            client=chat_client,
            name="wfm-intent-classifier",
        )
        sql_builder_agent = Agent(
            client=chat_client,
            name="wfm-sql-builder",
            tools=[mcp_schema],
        )
        query_executor_agent = Agent(
            client=chat_client,
            name="wfm-query-executor",
            tools=[mcp_exec],
        )

        # ``usage_tracker`` lives across the whole process so the same
        # counters surface in metrics regardless of which request
        # triggers a step. The dict is mutated by the workflow
        # executors; we never read it directly here.
        usage_tracker: dict[str, object] = {}
        workflow = build_workflow(
            intent_agent=intent_agent,
            sql_builder_agent=sql_builder_agent,
            query_executor_agent=query_executor_agent,
            bu_id=settings.bu_id_default,
            usage_tracker=usage_tracker,
        )

        # Stash the long-lived resources on ``app.state`` so request
        # handlers (``/healthz``, future identity middleware) can reach
        # them without re-importing this module.
        app.state.workflow = workflow
        app.state.history_provider = history_provider
        app.state.usage_tracker = usage_tracker

        add_agent_framework_fastapi_endpoint(app, workflow, path=AGUI_PATH)

        logger.info(
            "lifespan: workflow ready; AG-UI endpoint mounted at %s", AGUI_PATH
        )
        yield


__all__ = ["AGUI_PATH", "lifespan"]
