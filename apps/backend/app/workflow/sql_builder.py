"""SQL builder step.

The :class:`SqlBuilderStep` is the second executor in the workflow. It
receives an :class:`IntentBundle`, asks the SQL builder agent to turn the
resolved question into a single SELECT statement (under a
``response_format=SqlPlan`` constraint, with the MCP ``listTables`` and
``getSchema`` tools available for catalog + schema discovery per PLAN.md
decision D10), and emits a :class:`SqlBundle` for the final executor.

The agent is responsible for refusing unsafe / malformed asks and for
applying the mandatory BU scope filter — this module is plumbing only.
"""

from __future__ import annotations

import json

from agent_framework import Agent, Executor, WorkflowContext, handler

from app.workflow._helpers import (
    build_messages,
    extract_structured_text,
    render_template,
    track_usage,
)
from app.workflow.prompts import SQL_BUILDER_INSTRUCTIONS_TPL
from app.workflow.schemas import IntentBundle, SqlBundle, SqlPlan


class SqlBuilderStep(Executor):
    """Second workflow step: turn the resolved question into a ``SqlPlan``."""

    def __init__(
        self,
        agent: Agent,
        usage_tracker: dict,
        id: str = "sql_builder_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._usage = usage_tracker

    @handler
    async def run(
        self,
        bundle: IntentBundle,
        ctx: WorkflowContext[SqlBundle],
    ) -> None:
        rendered = render_template(
            SQL_BUILDER_INSTRUCTIONS_TPL,
            intentResult=json.dumps(bundle.intent_result.model_dump()),
            buId=bundle.bu_id,
            userQuestion=bundle.user_question,  # already resolved
        )
        messages = build_messages(rendered, bundle.user_question)
        response = await self._agent.run(
            messages,
            options={"response_format": SqlPlan},
        )
        track_usage(self._usage, "sql_builder", response)
        plan = SqlPlan.model_validate_json(extract_structured_text(response))
        await ctx.send_message(
            SqlBundle(
                sql_plan=plan,
                user_language=bundle.intent_result.language_hint,
                user_question=bundle.user_question,
            )
        )


__all__ = ["SqlBuilderStep"]
