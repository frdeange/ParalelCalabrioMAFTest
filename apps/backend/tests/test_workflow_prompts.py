"""Snapshot-style tests for the workflow prompt templates.

These tests pin down the exact string each executor sends as the system
prompt for a fixed input. The acceptance criterion of issue #8 calls for
"snapshot tests of each Executor's prompt with a fixed input"; we keep
them as plain string assertions to avoid pulling in a snapshot library.

If a template changes intentionally, update the expected substring(s)
below in the same PR — the diff makes the prompt change reviewable.
"""

from __future__ import annotations

import json

from app.workflow._helpers import render_template
from app.workflow.prompts import (
    INTENT_INSTRUCTIONS_TPL,
    QUERY_EXECUTOR_INSTRUCTIONS_TPL,
    SQL_BUILDER_INSTRUCTIONS_TPL,
)
from app.workflow.schemas import IntentResult, SqlPlan

# ---------------------------------------------------------------------------
# INTENT — verbatim constant (no rendering)
# ---------------------------------------------------------------------------


def test_intent_prompt_is_a_stable_constant() -> None:
    assert INTENT_INSTRUCTIONS_TPL.startswith(
        "You are the Intent Classifier inside a controlled WFM data workflow."
    )
    # Core invariants the classifier depends on:
    assert "resolved_question" in INTENT_INSTRUCTIONS_TPL
    assert "DataQuery, Conversational, OutOfScope" in INTENT_INSTRUCTIONS_TPL
    assert "language_hint" in INTENT_INSTRUCTIONS_TPL
    # PLAN decision D10: the intent classifier does NOT touch MCP.
    assert "listTables" not in INTENT_INSTRUCTIONS_TPL
    assert "candidate_tables" not in INTENT_INSTRUCTIONS_TPL
    assert "You have NO tools." in INTENT_INSTRUCTIONS_TPL


# ---------------------------------------------------------------------------
# SQL BUILDER — rendered with a fixed IntentResult
# ---------------------------------------------------------------------------


def _fixed_intent_result() -> IntentResult:
    return IntentResult(
        intent="DataQuery",
        language_hint="en",
        cache_action="reuse",
        resolved_question="How many active agents do we have today?",
    )


def test_sql_builder_prompt_renders_placeholders() -> None:
    rendered = render_template(
        SQL_BUILDER_INSTRUCTIONS_TPL,
        intentResult=json.dumps(_fixed_intent_result().model_dump()),
        buId=1,
        userQuestion="How many active agents do we have today?",
    )

    # No raw placeholders left.
    assert "{{intentResult}}" not in rendered
    assert "{{buId}}" not in rendered
    assert "{{userQuestion}}" not in rendered

    # Fixed-input values are present.
    assert '"intent": "DataQuery"' in rendered
    assert "How many active agents do we have today?" in rendered
    assert "WHERE bu_id = 1" in rendered

    # PLAN decision D10 invariants: SqlBuilder owns table discovery and never
    # depends on ``intentResult.candidate_tables``.
    assert "candidate_tables" not in rendered
    assert "listTables" in rendered
    assert "getSchema" in rendered
    assert "Forbidden: INSERT, UPDATE, DELETE" in rendered


# ---------------------------------------------------------------------------
# QUERY EXECUTOR — rendered with a fixed SqlPlan
# ---------------------------------------------------------------------------


def _fixed_sql_plan() -> SqlPlan:
    return SqlPlan(
        sql="SELECT COUNT(*) FROM agents WHERE bu_id = 1",
        tables_used=["agents"],
        assumptions=[],
        explanation="Counts active agents in BU 1.",
        error=None,
    )


def test_query_executor_prompt_renders_placeholders() -> None:
    rendered = render_template(
        QUERY_EXECUTOR_INSTRUCTIONS_TPL,
        sqlPlan=json.dumps(_fixed_sql_plan().model_dump()),
        userLanguage="es",
    )

    assert "{{sqlPlan}}" not in rendered
    assert "{{userLanguage}}" not in rendered

    assert '"sql": "SELECT COUNT(*) FROM agents WHERE bu_id = 1"' in rendered
    assert '"tables_used": ["agents"]' in rendered
    # The language hint must reach the prompt verbatim so the model
    # answers in the user's language.
    assert "userLanguage: es" in rendered

    # Invariants the executor relies on:
    assert "executeQuery (MCP)" in rendered
    assert "recall_conversation (function)" in rendered
