"""Schema + assembly tests for :mod:`app.workflow`.

Confirms that:

- The four pydantic models load with the expected defaults and reject
  malformed input.
- :func:`build_workflow` returns an object with a ``.run`` coroutine — we
  don't execute it (that needs a live LLM); we just check the wiring.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.workflow import (
    IntentBundle,
    IntentResult,
    SqlBundle,
    SqlPlan,
    build_workflow,
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def test_intent_result_defaults() -> None:
    ir = IntentResult(intent="Conversational")
    assert ir.intent == "Conversational"
    assert ir.language_hint == "en"
    assert ir.cache_action == "reuse"
    assert ir.resolved_question == ""


def test_intent_result_has_no_candidate_tables_field() -> None:
    # PLAN decision D10: the intent classifier no longer carries schema
    # metadata. ``candidate_tables`` must not exist on the model so a stray
    # downstream consumer fails loudly instead of silently using ``[]``.
    assert "candidate_tables" not in IntentResult.model_fields


def test_intent_result_requires_intent() -> None:
    with pytest.raises(ValidationError):
        IntentResult()  # type: ignore[call-arg]


def test_sql_plan_defaults() -> None:
    plan = SqlPlan()
    assert plan.sql == ""
    assert plan.tables_used == []
    assert plan.assumptions == []
    assert plan.explanation == ""
    assert plan.error is None


def test_sql_plan_round_trips_through_json() -> None:
    original = SqlPlan(
        sql="SELECT 1",
        tables_used=["t"],
        assumptions=["a"],
        explanation="x",
        error=None,
    )
    restored = SqlPlan.model_validate_json(original.model_dump_json())
    assert restored == original


def test_intent_bundle_requires_all_fields() -> None:
    bundle = IntentBundle(
        user_question="q",
        original_question="raw q",
        bu_id=1,
        intent_result=IntentResult(intent="DataQuery"),
    )
    assert bundle.bu_id == 1
    assert bundle.intent_result.intent == "DataQuery"


def test_sql_bundle_requires_all_fields() -> None:
    bundle = SqlBundle(
        sql_plan=SqlPlan(sql="SELECT 1"),
        user_language="es",
        user_question="q",
    )
    assert bundle.user_language == "es"
    assert bundle.sql_plan.sql == "SELECT 1"


# ---------------------------------------------------------------------------
# build_workflow — wiring smoke test
# ---------------------------------------------------------------------------


class _DummyAgent:
    """Stand-in for ``agent_framework.Agent`` — never gets called.

    ``SequentialBuilder.build()`` only inspects ``participants`` to wire
    them up; it does not invoke any agent at construction time. The dummy
    is just present so the executors' ``__init__`` succeeds.
    """


def test_build_workflow_returns_runnable_object() -> None:
    workflow = build_workflow(
        intent_agent=_DummyAgent(),  # type: ignore[arg-type]
        sql_builder_agent=_DummyAgent(),  # type: ignore[arg-type]
        query_executor_agent=_DummyAgent(),  # type: ignore[arg-type]
        bu_id=1,
        usage_tracker={},
    )

    assert hasattr(workflow, "run"), "MAF workflow must expose .run(history)"
    assert inspect.iscoroutinefunction(workflow.run) or callable(workflow.run)
