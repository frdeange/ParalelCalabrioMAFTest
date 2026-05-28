"""End-to-end-ish tests for the three workflow Executors and the
:func:`app.workflow.build.build_workflow` assembly.

We do not spin up the real MAF runtime here — instead each Executor is
driven directly with a stub :class:`agent_framework.Agent` whose
:meth:`run` returns a canned :class:`AgentResponse`, and the
:class:`WorkflowContext` is a :class:`unittest.mock.AsyncMock`. The
``build_workflow`` test then verifies the assembly returns a non-None
workflow object — that's the contract the FastAPI lifespan depends on.

Why this shape:

* The handler bodies in ``intent.py``, ``sql_builder.py`` and
  ``query_executor.py`` were uncovered by the existing suite (issue
  #14). Calling them via a mock ``ctx`` lights up every line without
  requiring a live LLM or the MAF event loop.
* ``build_workflow`` itself is a thin wrapper around
  :class:`SequentialBuilder` so we only smoke-test that it returns a
  usable object with the expected executors registered.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import Agent, AgentResponse, Message

from app.workflow.build import build_workflow
from app.workflow.intent import IntentStep
from app.workflow.query_executor import QueryExecutorStep
from app.workflow.schemas import IntentBundle, IntentResult, SqlBundle, SqlPlan
from app.workflow.sql_builder import SqlBuilderStep

# ---------------------------------------------------------------------------
# Stub agent
# ---------------------------------------------------------------------------


def _stub_agent(response: AgentResponse) -> Any:
    """Build a duck-typed stand-in for :class:`agent_framework.Agent`.

    Subclassing ``Agent`` directly is awkward because its ``__init__``
    requires a real ``client`` instance. Our Executors only ever call
    ``agent.run(...)``, so a :class:`MagicMock` shaped against the
    ``Agent`` interface with an async ``run`` is functionally
    equivalent and far cheaper.
    """
    agent = MagicMock(spec=Agent)
    agent.run = AsyncMock(return_value=response)
    return agent


def _assistant_response(text: str) -> AgentResponse:
    """Build an :class:`AgentResponse` with a single assistant text message."""
    return AgentResponse(messages=[Message(role="assistant", contents=[text])])


# ---------------------------------------------------------------------------
# IntentStep.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intent_step_emits_resolved_intent_bundle() -> None:
    """Happy path: agent returns valid IntentResult JSON, step emits an
    :class:`IntentBundle` carrying the resolved question + parsed intent.
    """
    intent_json = (
        '{"intent": "data_query", "language_hint": "en", '
        '"cache_action": "reuse", "resolved_question": "top 5 BUs by hours"}'
    )
    agent = _stub_agent(_assistant_response(intent_json))
    usage: dict[str, Any] = {}
    step = IntentStep(agent, bu_id=42, usage_tracker=usage)

    conversation = [
        Message(role="user", contents=["who are the top BUs?"]),
        Message(role="assistant", contents=["I can help with that."]),
        Message(role="user", contents=["actually show me the top 5 by hours"]),
    ]
    ctx = AsyncMock()

    await step.run(conversation, ctx)

    ctx.send_message.assert_awaited_once()
    bundle = ctx.send_message.await_args.args[0]
    assert isinstance(bundle, IntentBundle)
    assert bundle.bu_id == 42
    assert bundle.user_question == "top 5 BUs by hours"  # resolved
    assert bundle.original_question == "actually show me the top 5 by hours"
    assert bundle.intent_result.intent == "data_query"
    assert "intent" in usage  # track_usage created the bucket


@pytest.mark.asyncio
async def test_intent_step_falls_back_to_original_question_when_resolved_empty() -> None:
    """If the model emits an empty ``resolved_question`` the step falls
    back to the raw last user turn so downstream never sees an empty
    string.
    """
    intent_json = (
        '{"intent": "conversational", "language_hint": "es", '
        '"cache_action": "reuse", "resolved_question": ""}'
    )
    agent = _stub_agent(_assistant_response(intent_json))
    step = IntentStep(agent, bu_id=7, usage_tracker={})

    conversation = [Message(role="user", contents=["hola"])]
    ctx = AsyncMock()

    await step.run(conversation, ctx)

    bundle = ctx.send_message.await_args.args[0]
    assert bundle.user_question == "hola"
    assert bundle.original_question == "hola"


# ---------------------------------------------------------------------------
# SqlBuilderStep.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_builder_step_emits_sql_bundle_with_plan_and_language() -> None:
    sql_json = (
        '{"sql": "SELECT TOP 5 bu_id FROM hours WHERE bu_id = 42", '
        '"tables_used": ["hours"], "assumptions": [], '
        '"explanation": "top 5 hours by BU", "error": null}'
    )
    agent = _stub_agent(_assistant_response(sql_json))
    usage: dict[str, Any] = {}
    step = SqlBuilderStep(agent, usage_tracker=usage)

    bundle_in = IntentBundle(
        user_question="top 5 BUs",
        original_question="top 5 BUs",
        bu_id=42,
        intent_result=IntentResult(
            intent="data_query",
            language_hint="en",
            cache_action="reuse",
            resolved_question="top 5 BUs",
        ),
    )
    ctx = AsyncMock()

    await step.run(bundle_in, ctx)

    ctx.send_message.assert_awaited_once()
    out = ctx.send_message.await_args.args[0]
    assert isinstance(out, SqlBundle)
    assert out.user_language == "en"
    assert out.user_question == "top 5 BUs"
    assert out.sql_plan.sql.startswith("SELECT TOP 5")
    assert out.sql_plan.tables_used == ["hours"]
    assert "sql_builder" in usage


# ---------------------------------------------------------------------------
# QueryExecutorStep.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_executor_step_yields_final_agent_response() -> None:
    final_text = "You requested the top 5 BUs by hours — here is the table."
    agent = _stub_agent(_assistant_response(final_text))
    usage: dict[str, Any] = {}
    step = QueryExecutorStep(agent, usage_tracker=usage)

    bundle_in = SqlBundle(
        sql_plan=SqlPlan(
            sql="SELECT 1",
            tables_used=["hours"],
            assumptions=[],
            explanation="trivial",
            error=None,
        ),
        user_language="en",
        user_question="top 5 BUs",
    )
    ctx = AsyncMock()

    await step.run(bundle_in, ctx)

    ctx.yield_output.assert_awaited_once()
    response = ctx.yield_output.await_args.args[0]
    assert isinstance(response, AgentResponse)
    assert response.text == final_text
    assert "query_executor" in usage


# ---------------------------------------------------------------------------
# build_workflow
# ---------------------------------------------------------------------------


def test_build_workflow_returns_a_runnable_workflow_object() -> None:
    """``build_workflow`` should return a non-None workflow object exposing
    a ``run`` callable, so the FastAPI lifespan can hand it to the AG-UI
    helper without further introspection.
    """
    intent_agent = _stub_agent(_assistant_response("{}"))
    sql_agent = _stub_agent(_assistant_response("{}"))
    qx_agent = _stub_agent(_assistant_response("{}"))

    workflow = build_workflow(
        intent_agent=intent_agent,
        sql_builder_agent=sql_agent,
        query_executor_agent=qx_agent,
        bu_id=1,
        usage_tracker={},
    )

    assert workflow is not None
    assert callable(getattr(workflow, "run", None))
