"""Workflow assembly: wire the three Executors into a ``SequentialBuilder``.

A single :func:`build_workflow` factory keeps the assembly in one place so
the FastAPI app (issue #12) and the legacy REPL (``main_local_multiturn``)
share an identical workflow object — no risk of one drifting from the
other.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Agent
from agent_framework.orchestrations import SequentialBuilder

from app.workflow.intent import IntentStep
from app.workflow.query_executor import QueryExecutorStep
from app.workflow.sql_builder import SqlBuilderStep


def build_workflow(
    *,
    intent_agent: Agent,
    sql_builder_agent: Agent,
    query_executor_agent: Agent,
    bu_id: int,
    usage_tracker: dict[str, Any],
) -> Any:
    """Return a built MAF workflow ready to call ``.run(history)`` on.

    The returned type is whatever
    :meth:`agent_framework.orchestrations.SequentialBuilder.build` produces
    (kept as ``Any`` to avoid leaking MAF internals through our boundary;
    the workflow is opaque to callers — they only use ``.run``).
    """
    return SequentialBuilder(
        participants=[
            IntentStep(intent_agent, bu_id=bu_id, usage_tracker=usage_tracker),
            SqlBuilderStep(sql_builder_agent, usage_tracker=usage_tracker),
            QueryExecutorStep(query_executor_agent, usage_tracker=usage_tracker),
        ]
    ).build()


__all__ = ["build_workflow"]
