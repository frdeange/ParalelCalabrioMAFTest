"""Query executor + formatter step.

The :class:`QueryExecutorStep` is the third and final executor in the
workflow. It receives a :class:`SqlBundle`, asks the query executor agent
to either run the SQL via the ``executeQuery`` MCP tool or fall back to
``recall_conversation`` for meta-questions, and yields the final
user-facing :class:`AgentResponse` as the workflow's output.

The recall path is wired up by the caller via tool composition on the
agent — this module is plumbing only.
"""

from __future__ import annotations

import json
from typing import Never

from agent_framework import Agent, AgentResponse, Executor, WorkflowContext, handler

from app.workflow._helpers import build_messages, render_template, track_usage
from app.workflow.prompts import QUERY_EXECUTOR_INSTRUCTIONS_TPL
from app.workflow.schemas import SqlBundle


class QueryExecutorStep(Executor):
    """Final workflow step: execute the SQL plan and produce the answer."""

    def __init__(
        self,
        agent: Agent,
        usage_tracker: dict,
        id: str = "query_executor_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._usage = usage_tracker

    @handler
    async def run(
        self,
        bundle: SqlBundle,
        ctx: WorkflowContext[Never, AgentResponse],
    ) -> None:
        rendered = render_template(
            QUERY_EXECUTOR_INSTRUCTIONS_TPL,
            sqlPlan=json.dumps(bundle.sql_plan.model_dump()),
            userLanguage=bundle.user_language,
        )
        messages = build_messages(rendered, bundle.user_question)
        response = await self._agent.run(messages)
        track_usage(self._usage, "query_executor", response)
        await ctx.yield_output(AgentResponse(messages=response.messages))


__all__ = ["QueryExecutorStep"]
