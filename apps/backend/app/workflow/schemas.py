"""Pydantic message schemas exchanged between the workflow Executors.

The four models below define the typed boundary between the three steps:

- :class:`IntentResult` is the structured output of the intent classifier
  (LLM ``response_format``).
- :class:`SqlPlan` is the structured output of the SQL builder.
- :class:`IntentBundle` and :class:`SqlBundle` are the in-flight messages
  passed via ``WorkflowContext.send_message`` between executors; they
  carry the model output plus any context downstream steps need.

Keeping the schemas in a separate module avoids circular imports between
the Executors and lets unit tests instantiate them without spinning up
the agents.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IntentResult(BaseModel):
    """Structured output of the intent classifier step.

    Per PLAN.md decision D10 the classifier does **not** populate any
    schema metadata (tables/columns): it only labels the user turn and
    produces a standalone restatement. The downstream ``SqlBuilderStep``
    discovers tables through the MCP ``listTables`` tool on its own.

    ``extra='forbid'`` so that any drift (e.g. a legacy ``candidate_tables``
    key still being emitted by an older agent or test fixture) raises a
    :class:`pydantic.ValidationError` immediately instead of silently
    dropping the field.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str
    language_hint: str = "en"
    cache_action: str = "reuse"
    # Standalone, conversation-independent restatement of the user's latest
    # turn. Required so downstream steps never need to look at conversation
    # history themselves.
    resolved_question: str = ""


class SqlPlan(BaseModel):
    """Structured output of the SQL builder step."""

    sql: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    explanation: str = ""
    error: str | None = None


class IntentBundle(BaseModel):
    """Message sent from :class:`IntentStep` to :class:`SqlBuilderStep`."""

    user_question: str  # resolved (standalone) — what downstream consumes
    original_question: str  # raw last user turn — kept for audit only
    bu_id: int
    intent_result: IntentResult


class SqlBundle(BaseModel):
    """Message sent from :class:`SqlBuilderStep` to :class:`QueryExecutorStep`."""

    sql_plan: SqlPlan
    user_language: str
    user_question: str  # same as IntentBundle.user_question (resolved)
