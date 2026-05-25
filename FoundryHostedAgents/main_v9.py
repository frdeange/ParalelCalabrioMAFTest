"""Variant v9 — v7 wrapped as a Foundry Hosted Agent (Responses protocol).

What this is
------------
v7 logic, exact same components (FoundryChatClient + 3 ``Agent`` instances +
Local ``MCPStreamableHTTPTool`` + custom executors + ``AuditHistoryProvider``),
hosted via ``ResponsesHostServer`` on http://localhost:8088.

Why this variant
~~~~~~~~~~~~~~~~
Foundry Hosted Agents are a separate hosting model from PromptAgent. Instead
of a declarative definition stored in Foundry, the entire MAF agent / workflow
runs inside a container managed by Foundry — your code is the agent. This
sidesteps every PromptAgent constraint we hit in v5/v8 (no client tools, no
per-call response_format, missing first-agent telemetry) because the request
is served by your local code — there is no service-side agent runtime in the
path.

Net effect:

- All 27+ v7 spans should still land in App Insights (same Local MCP path).
- The workflow becomes addressable as an Agent via ``/responses``.
- When eventually deployed via ``azd deploy``, this same code is packaged into
  a container and shown in Foundry Studio.

Lifecycle
~~~~~~~~~
``ResponsesHostServer`` registers a ``shutdown_handler`` that calls the agent's
``__aexit__`` (see ``_responses.py`` line ~365). Because ``WorkflowAgent``
inherits from ``BaseAgent`` (no async-context-manager hooks), we cannot rely
on the host to enter our MCP tools transitively through the wrapper. Instead
we open the MCP sessions ourselves via ``AsyncExitStack`` BEFORE
``await server.run_async()`` and let the stack close on shutdown / Ctrl-C.

Run locally
-----------
::

    python main_v9.py
    # then in another terminal:
    curl -X POST http://localhost:8088/responses \\
      -H "Content-Type: application/json" \\
      -d '{"input": "\u00bfCu\u00e1ntos agentes hay en mi organizaci\u00f3n?"}'
"""

from __future__ import annotations

import os
import asyncio
import json
from collections.abc import Sequence
from contextlib import AsyncExitStack
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing_extensions import Never

from azure.identity.aio import DefaultAzureCredential
from agent_framework import (
    Agent,
    AgentResponse,
    Executor,
    MCPStreamableHTTPTool,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework._sessions import HistoryProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from agent_framework.observability import create_resource, enable_instrumentation
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.monitor.opentelemetry import configure_azure_monitor

load_dotenv()

# Telemetry bootstrap. When deployed to Foundry, the `microsoft-opentelemetry`
# distro already configures Tracer/Meter/Logger providers from the injected
# APPLICATIONINSIGHTS_CONNECTION_STRING. Calling `configure_azure_monitor`
# again in that environment triggers harmless "Overriding of current
# *Provider is not allowed" warnings. To keep the same code working locally
# *and* hosted, only configure when Foundry's hosted bit is NOT set.
_IS_HOSTED = bool(os.getenv("AGENT_SERVER_HOSTED")) or bool(
    os.getenv("FOUNDRY_AGENT_NAME")
)
if not _IS_HOSTED:
    configure_azure_monitor(
        connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
        resource=create_resource(),
        enable_live_metrics=True,
    )
enable_instrumentation(enable_sensitive_data=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Prefer the Foundry-injected env var so deployments pick up the correct model
# automatically. Fall back to FOUNDRY_MODEL for local dev, then to a sane
# default.
FOUNDRY_MODEL = (
    os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or os.getenv("FOUNDRY_MODEL")
    or "gpt-5.2"
)
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL", "https://4x59q5fx-8001.uks1.devtunnels.ms/mcp/"
)
# BU scope is a config concern for this experiment, not extracted from each
# inbound HTTP request. To make it per-call later, parse it out of the request
# in a custom Invocations handler or smuggle it via a custom header.
BU_ID = int(os.getenv("BU_ID", "1"))


# ---------------------------------------------------------------------------
# Schemas (same as v7)
# ---------------------------------------------------------------------------

class IntentResult(BaseModel):
    intent: str
    candidate_tables: list[str] = Field(default_factory=list)
    language_hint: str = "en"
    cache_action: str = "reuse"


class SqlPlan(BaseModel):
    sql: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    explanation: str = ""
    error: str | None = None


class IntentBundle(BaseModel):
    user_question: str
    bu_id: int
    intent_result: IntentResult


class SqlBundle(BaseModel):
    sql_plan: SqlPlan
    user_language: str
    user_question: str


# ---------------------------------------------------------------------------
# Instructions (identical to v7)
# ---------------------------------------------------------------------------

INTENT_INSTRUCTIONS_TPL = """\
You are the Intent Classifier inside a controlled WFM data workflow.

Goal:
- Classify the user turn as exactly one of: DataQuery, Conversational, OutOfScope.
- Stay domain-neutral. Never rely on hidden business knowledge, hardcoded table names, or invented schema.

Rules:
1. Choose DataQuery only when the user needs live data, counts, filters, trends, records, or verification from the database.
2. Choose Conversational for greetings, clarifications, help text, or general discussion that does not require live data.
3. Choose OutOfScope for requests outside the approved WFM data assistant scope, unsafe asks, or requests the workflow cannot satisfy.
4. For DataQuery, call the listTables MCP tool first and populate candidate_tables only with tables it returns. Never invent table names.
5. The user may write in any language. Detect the language of the user message and set language_hint to its BCP-47 code (e.g. "en", "es").
6. Set cache_action to "reuse" by default; use "refresh" only when the user explicitly asks for fresh data or signals the cached answer is stale.
7. For Conversational or OutOfScope turns, return candidate_tables as an empty array.
8. Never invent columns, joins, or business rules.
"""

SQL_BUILDER_INSTRUCTIONS_TPL = """\
You are the SQL Builder inside a controlled WFM data workflow.

Mission:
- Convert the original user question into ONE safe SQL Server SELECT statement.
- Use ONLY the structured inputs supplied here and the metadata returned by MCP tools.
- Stay domain-neutral. The provided metadata is the only structural truth.

Inputs for this turn:
- intentResult: {{intentResult}}
- buId: {{buId}}
- userQuestion: {{userQuestion}}

Mandatory rules:
1. On success, produce a single valid SELECT statement in `sql` and set `error` to null. On failure, set `sql` to "", `tables_used` to [], `assumptions` to [], explain why in `explanation`, and populate `error`.
2. Use ONLY tables shortlisted in `intentResult.candidate_tables`.
3. For each candidate table, call the getSchema MCP tool to retrieve column definitions and join hints before generating SQL. Do not assume schema.
4. Use ONLY columns and joins confirmed by getSchema results. Never invent columns, joins, filters, aliases, KPIs, or business logic.
5. Apply the mandatory BU scope filter: every query MUST constrain results to `buId` (e.g. WHERE bu_id = {{buId}} on the appropriate table).
6. Keep the query minimal: only needed columns, only needed joins, clear predicates, no comments, no markdown.
7. Forbidden: INSERT, UPDATE, DELETE, DROP, ALTER, MERGE, EXEC, temp-table writes, dynamic SQL, multiple statements.
8. If metadata is missing, ambiguous, or insufficient, do not guess. Return an error instead of fabricating structure.
9. `tables_used` must list every table referenced in `sql`. `assumptions` should be empty unless you applied a defensible inference that the user should know about.
"""

QUERY_EXECUTOR_INSTRUCTIONS_TPL = """\
You are the Query Executor and Formatter inside a controlled WFM data workflow.

Mission:
- Execute the SQL query from the plan using the executeQuery MCP tool.
- Produce the final user-facing answer from the execution results.
- Never invent facts. Speak only from the actual query results.
- Respond in the language indicated by `userLanguage` (BCP-47).

Inputs for this turn:
- sqlPlan: {{sqlPlan}}
- userLanguage: {{userLanguage}}

Rules:
1. If `sqlPlan.error` is not null or `sqlPlan.sql` is empty, do NOT call executeQuery. Give a short, non-technical message stating the request could not be processed and, when useful, hint at what additional info would help.
2. Otherwise, call executeQuery with `sqlPlan.sql` and treat the returned rows as the only source of truth.
3. If the query returns 0 rows, state clearly that no matching records were found in the allowed scope.
4. Summarize key counts, trends, or highlights that are directly supported by the rows. Be concise, accurate, and helpful.
5. If execution fails, give a short non-technical recovery message. Never expose SQL, stack traces, or internal identifiers.
6. Never claim the query ran if the execution result is missing or failed.
7. Output plain natural language only. No markdown tables unless the user explicitly requested tabular output.
"""


def _render(template: str, **vars: object) -> str:
    out = template
    for key, value in vars.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


# ---------------------------------------------------------------------------
# Audit-only HistoryProvider (cross-agent in-memory store)
# ---------------------------------------------------------------------------

class AuditHistoryProvider(HistoryProvider):
    """Same provider as v7. The host warns about in-memory state being lost
    between container deactivations — fine for local dev; switch to Cosmos
    or a similar backend before deploying via ``azd deploy``."""

    DEFAULT_SOURCE_ID = "audit_log"

    def __init__(self, *, source_id: str = "audit_log") -> None:
        super().__init__(
            source_id=source_id,
            load_messages=False,
            store_inputs=True,
            store_outputs=True,
            store_context_messages=True,
        )
        self._log: list[dict[str, Any]] = []

    async def get_messages(  # type: ignore[override]
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        return []

    async def save_messages(  # type: ignore[override]
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        for m in messages:
            self._log.append(
                {
                    "session_id": session_id,
                    "role": getattr(m, "role", None),
                    "author": getattr(m, "author_name", None),
                    "text": getattr(m, "text", None),
                }
            )

    @property
    def log(self) -> list[dict[str, Any]]:
        return list(self._log)


# ---------------------------------------------------------------------------
# Helpers (identical to v7)
# ---------------------------------------------------------------------------

def _extract_structured_text(response: AgentResponse) -> str:
    decoder = json.JSONDecoder()
    for msg in reversed(getattr(response, "messages", []) or []):
        if getattr(msg, "role", None) != "assistant":
            continue
        for content in reversed(getattr(msg, "contents", None) or []):
            data = content.to_dict() if hasattr(content, "to_dict") else {}
            if data.get("type") != "text":
                continue
            text = (data.get("text") or "").strip()
            idx = text.find("{")
            if idx < 0:
                continue
            try:
                obj, _end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            return json.dumps(obj)
    return response.text


def _build_messages(system_text: str, user_text: str) -> list[Message]:
    return [
        Message(role="system", contents=[system_text]),
        Message(role="user", contents=[user_text]),
    ]


# ---------------------------------------------------------------------------
# Custom executors (identical to v7, but without per-call usage tracking —
# tokens are already captured by MAF instrumentation as span attributes)
# ---------------------------------------------------------------------------

class IntentStep(Executor):
    def __init__(self, agent: Agent, bu_id: int, id: str = "intent_step") -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id

    @handler
    async def run(
        self,
        conversation: list[Message],
        ctx: WorkflowContext[IntentBundle],
    ) -> None:
        user_question = next(
            (m.text for m in reversed(conversation) if m.role == "user" and m.text),
            "",
        )
        messages = _build_messages(INTENT_INSTRUCTIONS_TPL, user_question)
        response = await self._agent.run(
            messages,
            options={"response_format": IntentResult},
        )
        intent = IntentResult.model_validate_json(_extract_structured_text(response))
        await ctx.send_message(
            IntentBundle(
                user_question=user_question,
                bu_id=self._bu_id,
                intent_result=intent,
            )
        )


class SqlBuilderStep(Executor):
    def __init__(self, agent: Agent, id: str = "sql_builder_step") -> None:
        super().__init__(id=id)
        self._agent = agent

    @handler
    async def run(
        self,
        bundle: IntentBundle,
        ctx: WorkflowContext[SqlBundle],
    ) -> None:
        rendered = _render(
            SQL_BUILDER_INSTRUCTIONS_TPL,
            intentResult=json.dumps(bundle.intent_result.model_dump()),
            buId=bundle.bu_id,
            userQuestion=bundle.user_question,
        )
        messages = _build_messages(rendered, bundle.user_question)
        response = await self._agent.run(
            messages,
            options={"response_format": SqlPlan},
        )
        plan = SqlPlan.model_validate_json(_extract_structured_text(response))
        await ctx.send_message(
            SqlBundle(
                sql_plan=plan,
                user_language=bundle.intent_result.language_hint,
                user_question=bundle.user_question,
            )
        )


class QueryExecutorStep(Executor):
    def __init__(self, agent: Agent, id: str = "query_executor_step") -> None:
        super().__init__(id=id)
        self._agent = agent

    @handler
    async def run(
        self,
        bundle: SqlBundle,
        ctx: WorkflowContext[Never, AgentResponse],
    ) -> None:
        rendered = _render(
            QUERY_EXECUTOR_INSTRUCTIONS_TPL,
            sqlPlan=json.dumps(bundle.sql_plan.model_dump()),
            userLanguage=bundle.user_language,
        )
        messages = _build_messages(rendered, bundle.user_question)
        response = await self._agent.run(messages)
        await ctx.yield_output(AgentResponse(messages=response.messages))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]

    async with DefaultAzureCredential() as credential, AsyncExitStack() as mcp_stack:
        client = FoundryChatClient(
            project_endpoint=project_endpoint,
            model=FOUNDRY_MODEL,
            credential=credential,
        )

        # Local MCP — entered eagerly so the connection is ready before the
        # first HTTP request. ``WorkflowAgent`` does not propagate
        # ``__aenter__`` to inner agents, so the host's lazy entry would never
        # reach the MCP tools.
        mcp_list = await mcp_stack.enter_async_context(
            MCPStreamableHTTPTool(
                name="wfm-data",
                url=MCP_SERVER_URL,
                allowed_tools=["listTables"],
                approval_mode="never_require",
            )
        )
        mcp_schema = await mcp_stack.enter_async_context(
            MCPStreamableHTTPTool(
                name="wfm-data",
                url=MCP_SERVER_URL,
                allowed_tools=["getSchema"],
                approval_mode="never_require",
            )
        )
        mcp_exec = await mcp_stack.enter_async_context(
            MCPStreamableHTTPTool(
                name="wfm-data",
                url=MCP_SERVER_URL,
                allowed_tools=["executeQuery"],
                approval_mode="never_require",
            )
        )

        audit = AuditHistoryProvider()

        # ``store=False`` per Foundry hosting guidance: the hosting layer
        # manages conversation history; storing again on the service side
        # would duplicate state.
        common_options = {"store": False}

        intent_agent = Agent(
            client=client,
            name="wfm-intent-classifier",
            tools=[mcp_list],
            context_providers=[audit],
            default_options=common_options,
        )
        sql_builder_agent = Agent(
            client=client,
            name="wfm-sql-builder",
            tools=[mcp_schema],
            context_providers=[audit],
            default_options=common_options,
        )
        query_executor_agent = Agent(
            client=client,
            name="wfm-query-executor",
            tools=[mcp_exec],
            context_providers=[audit],
            default_options=common_options,
        )

        workflow = SequentialBuilder(
            participants=[
                IntentStep(intent_agent, bu_id=BU_ID),
                SqlBuilderStep(sql_builder_agent),
                QueryExecutorStep(query_executor_agent),
            ]
        ).build()

        workflow_agent = workflow.as_agent(
            name="wfm-data-assistant",
            description="WFM data assistant: classifies intent, builds a "
            "scoped SQL query, executes it, and answers in the user's language.",
        )

        server = ResponsesHostServer(workflow_agent)
        print(
            "v9 host ready on http://localhost:8088/responses "
            f"(bu_id={BU_ID}, model={FOUNDRY_MODEL}). Ctrl-C to stop."
        )
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
