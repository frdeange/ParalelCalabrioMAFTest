"""Tests for the AG-UI ``POST /agui`` endpoint.

The production lifespan opens MCP sessions and an Azure credential —
neither is available in CI — so this module mounts the endpoint
against an in-memory stub via a custom lifespan. The stub satisfies
:class:`agent_framework_ag_ui.AgentFrameworkAgent` by subclassing it
and overriding :meth:`run` to yield a canned event sequence.

What we are actually verifying
------------------------------
* ``add_agent_framework_fastapi_endpoint`` is wired to the
  application *during* startup (i.e. inside the lifespan) — the route
  must therefore exist by the time the first request arrives.
* The endpoint returns a Server-Sent Events stream whose **first
  event** is ``RUN_STARTED``, matching the AG-UI contract the
  frontend (CopilotKit) depends on.
* Input validation: a request body that does not match
  ``AGUIRequest`` (e.g. no ``messages``) returns 422.

We rely on the real AG-UI helper so this test catches regressions in
the wiring even though the workflow itself is stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from ag_ui.core.events import (
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from agent_framework_ag_ui import AgentFrameworkAgent, add_agent_framework_fastapi_endpoint
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.lifespan import AGUI_PATH
from app.main import create_app

# ---------------------------------------------------------------------------
# Stub workflow
# ---------------------------------------------------------------------------


class _StubAgentFrameworkAgent(AgentFrameworkAgent):
    """``AgentFrameworkAgent`` whose :meth:`run` yields a fixed sequence.

    Subclassing the real wrapper means the AG-UI helper's
    ``isinstance`` dispatch picks the no-wrapping branch and calls
    our ``run`` directly. The base constructor still demands a
    ``SupportsAgentRun`` instance for ``agent=``; we hand it a
    :class:`MagicMock` because the base class only stores the
    reference — our override never delegates to it.
    """

    def __init__(self) -> None:
        super().__init__(agent=MagicMock(spec=["run", "id", "name", "description"]))

    async def run(  # type: ignore[override]
        self, input_data: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        thread_id = input_data.get("thread_id") or "thread-test"
        run_id = input_data.get("run_id") or "run-test"
        message_id = "msg-test"

        yield RunStartedEvent(thread_id=thread_id, run_id=run_id)
        yield TextMessageStartEvent(message_id=message_id, role="assistant")
        yield TextMessageContentEvent(message_id=message_id, delta="hello")
        yield TextMessageEndEvent(message_id=message_id)
        yield RunFinishedEvent(thread_id=thread_id, run_id=run_id)


def _stub_lifespan_factory() -> Any:
    """Build a lifespan that mounts the stub agent at :data:`AGUI_PATH`."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        stub = _StubAgentFrameworkAgent()
        add_agent_framework_fastapi_endpoint(app, stub, path=AGUI_PATH)
        app.state.workflow = stub
        yield

    return _lifespan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    app = create_app(lifespan=_stub_lifespan_factory())
    with TestClient(app) as c:
        yield c


def _agui_payload() -> dict[str, Any]:
    """Minimal but valid ``AGUIRequest`` body."""
    return {
        "messages": [{"id": "u-1", "role": "user", "content": "hi"}],
        "thread_id": "thread-test",
        "run_id": "run-test",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agui_endpoint_is_registered(client: TestClient) -> None:
    """Even before issuing a real call we expect the route to exist —
    this is the regression test for "did the lifespan run and mount
    the endpoint?"."""
    paths = {route.path for route in client.app.routes}  # type: ignore[attr-defined]
    assert AGUI_PATH in paths


def test_agui_first_event_is_run_started(client: TestClient) -> None:
    """The first SSE event the endpoint emits must be ``RUN_STARTED``.

    AG-UI uses this event to signal to CopilotKit that the assistant
    has begun processing. Anything else would break the client-side
    state machine.

    The :class:`ag_ui.encoder.EventEncoder` writes each event as a
    single ``data: {<json>}\\n\\n`` block — the ``type`` discriminator
    lives inside the JSON body, not in an SSE ``event:`` field — so we
    parse the first ``data:`` line to read the discriminator.
    """
    import json

    with client.stream("POST", AGUI_PATH, json=_agui_payload()) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        first_event_type: str | None = None
        for raw_line in response.iter_lines():
            if not raw_line or not raw_line.startswith("data:"):
                continue
            payload = json.loads(raw_line.split(":", 1)[1].strip())
            first_event_type = payload.get("type")
            break

    assert first_event_type == "RUN_STARTED"


def test_agui_rejects_invalid_body(client: TestClient) -> None:
    """A body missing the required ``messages`` field must surface as a
    422 — the AG-UI helper validates against ``AGUIRequest`` before
    invoking the workflow."""
    response = client.post(AGUI_PATH, json={"thread_id": "t", "run_id": "r"})
    assert response.status_code == 422
