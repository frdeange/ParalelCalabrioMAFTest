"""Variant E — FoundryChatClient + 3 independent agent calls + audit
HistoryProvider (no shared service conversation).

Why this exists
---------------
Variants A/C/D all pinned the three agents (intent, sql-builder, query-executor)
to the same Foundry-side conversation. MAF documentation is explicit that
``Sessions are agent/service-specific. Reusing a session with a different agent
configuration or provider can lead to invalid context.`` Empirically this
manifests as the hosted MCP tools getting dropped on every agent that joins the
shared conversation after the first one — the server fails to reattach the new
agent's tool set on the existing session.

This variant follows the canonical pattern instead:

1. NO shared service-side conversation. Each agent.run() is independent.
2. Inter-agent state is passed as structured Pydantic objects through the
   workflow graph (already how the rest of the pipeline works).
3. Cross-agent visibility for *audit / debugging* (not model context) is
   provided by a single ``AuditHistoryProvider`` instance shared by all three
   ``Agent`` definitions. It has ``load_messages=False`` so the model never
   sees other agents' turns, but ``store_inputs/outputs/context_messages=True``
   so every message flows into one in-memory list.
4. OpenTelemetry continues to correlate the three calls into one workflow
   trace via W3C ``traceparent`` propagation — that is the real E2E lane.
"""

from __future__ import annotations

import os
import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing_extensions import Never

from azure.identity.aio import DefaultAzureCredential

from agent_framework import (
    Agent,
    AgentResponse,
    Executor,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework._sessions import HistoryProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from agent_framework.observability import enable_sensitive_telemetry, configure_otel_providers, create_resource, enable_instrumentation
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace, metrics

load_dotenv()
configure_otel_providers()
# Wire telemetry to Azure Application Insights.
# ``configure_azure_monitor`` reads APPLICATIONINSIGHTS_CONNECTION_STRING from
# the environment, installs the AzureMonitor exporters (traces, logs, metrics)
# on the global OTel providers, and is the canonical way to ship MAF telemetry
# to App Insights. ``configure_otel_providers`` (the MAF helper) is for OTLP
# collectors / VS Code extension and does NOT export to App Insights on its own.
enable_sensitive_telemetry()

configure_azure_monitor(
    connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
    resource=create_resource(),  # Uses OTEL_SERVICE_NAME, etc.
    enable_live_metrics=True,
    )
enable_instrumentation(enable_sensitive_data=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOUNDRY_MODEL = os.getenv("FOUNDRY_MODEL", "gpt-5.2")
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL", "https://4x59q5fx-8001.uks1.devtunnels.ms/mcp/"
)


# ---------------------------------------------------------------------------
# Schemas (used both as response_format and as inter-step payloads)
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
# Instructions (templated with {{placeholder}} markers, rendered per-call)
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
    """Replace ``{{name}}`` markers with the str() of the supplied keyword values."""
    out = template
    for key, value in vars.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


# ---------------------------------------------------------------------------
# Audit-only HistoryProvider
# ---------------------------------------------------------------------------

class AuditHistoryProvider(HistoryProvider):
    """In-memory audit log for cross-agent visibility.

    This provider deliberately does NOT participate in the model's context:
    ``load_messages=False`` means ``get_messages`` is never consulted by the
    agent harness, so nothing this provider stores is replayed back to the
    model. We only piggyback on the ``after_run`` lifecycle to capture every
    input/output/context message produced by any agent we are attached to.

    All agents share a single instance. State lives in ``self._log`` (not in
    the session.state dict) so it survives across independent ``agent.run()``
    calls with different sessions.
    """

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
        # load_messages=False makes the harness skip this, but be explicit.
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
# Helpers
# ---------------------------------------------------------------------------

def _track_usage(tracker: dict, step: str, response: AgentResponse) -> None:
    usage = getattr(response, "usage_details", None) or {}
    bucket = tracker.setdefault(step, defaultdict(int))
    for key, value in usage.items():
        if isinstance(value, int):
            bucket[key] += value


def _extract_structured_text(response: AgentResponse) -> str:
    """Return the last text-typed content's JSON, skipping tool-call items."""
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
    """Build a ``[system, user]`` message list rendered per call."""
    return [
        Message(role="system", contents=[system_text]),
        Message(role="user", contents=[user_text]),
    ]


def _print_usage_report(tracker: dict, label: str) -> None:
    print(f"\n===== Token usage ({label}) =====")
    totals: dict[str, int] = defaultdict(int)
    for step, usage in tracker.items():
        parts = ", ".join(f"{k}={v}" for k, v in usage.items())
        print(f"  [{step}] {parts}")
        for k, v in usage.items():
            totals[k] += v
    print("  ---")
    parts = ", ".join(f"{k}={v}" for k, v in totals.items())
    print(f"  [TOTAL] {parts}")


def _print_audit_log(audit: AuditHistoryProvider) -> None:
    print("\n===== Audit log (cross-agent in-memory store) =====")
    for i, entry in enumerate(audit.log):
        author = entry.get("author") or entry.get("role") or "?"
        text = (entry.get("text") or "").strip().replace("\n", " ")
        if len(text) > 160:
            text = text[:160] + "…"
        print(f"  [{i:02d}] {author}: {text}")


# ---------------------------------------------------------------------------
# Custom executors (no shared conversation_id; just structured payloads)
# ---------------------------------------------------------------------------

class IntentStep(Executor):
    def __init__(
        self,
        agent: Agent,
        bu_id: int,
        usage_tracker: dict,
        id: str = "intent_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id
        self._usage = usage_tracker

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
        _track_usage(self._usage, "intent", response)
        intent = IntentResult.model_validate_json(_extract_structured_text(response))
        await ctx.send_message(
            IntentBundle(
                user_question=user_question,
                bu_id=self._bu_id,
                intent_result=intent,
            )
        )


class SqlBuilderStep(Executor):
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
        _track_usage(self._usage, "sql_builder", response)
        plan = SqlPlan.model_validate_json(_extract_structured_text(response))
        await ctx.send_message(
            SqlBundle(
                sql_plan=plan,
                user_language=bundle.intent_result.language_hint,
                user_question=bundle.user_question,
            )
        )


class QueryExecutorStep(Executor):
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
        rendered = _render(
            QUERY_EXECUTOR_INSTRUCTIONS_TPL,
            sqlPlan=json.dumps(bundle.sql_plan.model_dump()),
            userLanguage=bundle.user_language,
        )
        messages = _build_messages(rendered, bundle.user_question)
        response = await self._agent.run(messages)
        _track_usage(self._usage, "query_executor", response)
        await ctx.yield_output(AgentResponse(messages=response.messages))


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------

async def run_turn(
    user_question: str,
    bu_id: int,
    intent_agent: Agent,
    sql_builder_agent: Agent,
    query_executor_agent: Agent,
) -> tuple[AgentResponse | None, dict]:
    usage_tracker: dict = {}

    workflow = SequentialBuilder(
        participants=[
            IntentStep(intent_agent, bu_id=bu_id, usage_tracker=usage_tracker),
            SqlBuilderStep(sql_builder_agent, usage_tracker=usage_tracker),
            QueryExecutorStep(query_executor_agent, usage_tracker=usage_tracker),
        ]
    ).build()

    events = await workflow.run(user_question)
    outputs = events.get_outputs()
    return (outputs[0] if outputs else None), usage_tracker


async def main() -> None:
    user_question = os.getenv(
        "USER_QUESTION", "¿Cuántos agentes hay en mi organización?"
    )
    bu_id = int(os.getenv("BU_ID", "1"))

    async with DefaultAzureCredential() as credential:
        project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]

        # --- Single chat client reused by all three agents --------------------
        client = FoundryChatClient(
            project_endpoint=project_endpoint,
            model=FOUNDRY_MODEL,
            credential=credential,
        )

        # --- Hosted MCP tool configs (one allow-list per agent role) ----------
        mcp_list = client.get_mcp_tool(
            name="wfm-data",
            url=MCP_SERVER_URL,
            allowed_tools=["listTables"],
            approval_mode="never_require",
        )
        mcp_schema = client.get_mcp_tool(
            name="wfm-data",
            url=MCP_SERVER_URL,
            allowed_tools=["getSchema"],
            approval_mode="never_require",
        )
        mcp_exec = client.get_mcp_tool(
            name="wfm-data",
            url=MCP_SERVER_URL,
            allowed_tools=["executeQuery"],
            approval_mode="never_require",
        )

        # --- Shared audit-only history provider -------------------------------
        # One instance, attached to all three agents. load_messages=False keeps
        # it out of the model context; after_run() captures every input/output
        # into self._log so we have one place to inspect the whole workflow.
        audit = AuditHistoryProvider()

        # --- Three client-side agents (independent, no shared conv) -----------
        # Instructions are injected per call as a system Message so we can
        # render placeholders ({{intentResult}}, {{buId}}, {{sqlPlan}}, …)
        # fresh each turn.
        intent_agent = Agent(
            client=client,
            name="wfm-intent-classifier",
            tools=[mcp_list],
            context_providers=[audit],
        )
        sql_builder_agent = Agent(
            client=client,
            name="wfm-sql-builder",
            tools=[mcp_schema],
            context_providers=[audit],
        )
        query_executor_agent = Agent(
            client=client,
            name="wfm-query-executor",
            tools=[mcp_exec],
            context_providers=[audit],
        )

        final, usage = await run_turn(
            user_question=user_question,
            bu_id=bu_id,
            intent_agent=intent_agent,
            sql_builder_agent=sql_builder_agent,
            query_executor_agent=query_executor_agent,
        )

        if not final:
            print("No output produced by the workflow.")
            return

        print("\n===== Final Response =====")
        for msg in final.messages:
            author = msg.author_name or "assistant"
            print(f"[{author}]\n{msg.text}\n")

        _print_usage_report(
            usage,
            label="variant E — FoundryChatClient, independent agents + audit HistoryProvider",
        )
        _print_audit_log(audit)

    # Force-flush OTel exporters so spans / logs / metrics actually leave the
    # process before it exits. Without this, BatchSpanProcessor's queue can be
    # discarded on interpreter shutdown and the trace never reaches App Insights.
    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "force_flush"):
        tracer_provider.force_flush(timeout_millis=10_000)
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "force_flush"):
        meter_provider.force_flush(timeout_millis=10_000)


if __name__ == "__main__":
    asyncio.run(main())
