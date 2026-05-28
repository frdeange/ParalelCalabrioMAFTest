"""Three-step MAF workflow: Intent → SqlBuilder → QueryExecutor.

This package owns the orchestration logic that used to live as a single
flat module in :mod:`main_local_multiturn` at the repository root. Each
Executor lives in its own file so it can be unit-tested in isolation; the
shared schemas, prompt templates and helper utilities sit beside them.

Public surface (re-exported below):

- Executors: :class:`IntentStep`, :class:`SqlBuilderStep`, :class:`QueryExecutorStep`
- Schemas:   :class:`IntentResult`, :class:`SqlPlan`, :class:`IntentBundle`, :class:`SqlBundle`
- Builder:   :func:`build_workflow`
- Constant:  :data:`HISTORY_TURNS` (sliding window passed to the classifier)

See PLAN.md §6.1 for the architectural context and ADR-0001 for the
overall design rationale.
"""

from __future__ import annotations

from app.workflow._helpers import HISTORY_TURNS
from app.workflow.build import build_workflow
from app.workflow.intent import IntentStep
from app.workflow.query_executor import QueryExecutorStep
from app.workflow.schemas import IntentBundle, IntentResult, SqlBundle, SqlPlan
from app.workflow.sql_builder import SqlBuilderStep

__all__ = [
    "HISTORY_TURNS",
    "IntentBundle",
    "IntentResult",
    "IntentStep",
    "QueryExecutorStep",
    "SqlBuilderStep",
    "SqlBundle",
    "SqlPlan",
    "build_workflow",
]
