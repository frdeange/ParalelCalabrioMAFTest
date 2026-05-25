"""Variant D — same sequential pipeline, but built on top of ``FoundryChatClient``
+ ``Agent`` (client-side agent definitions) instead of ``FoundryAgent`` (portal-
defined Foundry agents).

Why this exists
---------------
With ``FoundryAgent``, the agent definition (model, instructions, tools, response
schema, structured inputs) lives in the Foundry portal. The SDK calls
``/agents/{id}/runs`` and the server is responsible for reattaching tools on
each turn. We observed a server-side regression where, when ``conversation_id``
is passed, the hosted MCP tools are silently dropped from the run (verified
empirically: ``sql-builder`` stops calling ``getSchema`` and ``query-executor``
stops calling ``executeQuery``).

With ``FoundryChatClient`` we call ``/responses`` directly with ``tools=[...]``
in every request. The tools cannot get dropped because they are part of the
request payload, not a server-side resource lookup. Everything else (model,
observability, conversation semantics) is identical.

What this file demonstrates
---------------------------
- Sequential workflow: intent -> sql-builder -> query-executor.
- Three client-side ``Agent`` instances backed by a single ``FoundryChatClient``.
- Pydantic models passed directly as ``response_format`` (no hand-written
  JSON schemas, no ``PromptAgentDefinitionTextOptions``).
- Hosted MCP tools attached via ``client.get_mcp_tool(...)``.
- Shared Foundry conversation across ALL three agents (variant A semantics).
- Per-call instructions injected via a leading system ``Message`` because the
  SDK skips the ``instructions=`` option when a ``conversation_id`` is set.
- OpenTelemetry / Foundry observability untouched.
"""

from __future__ import annotations

import os
import asyncio
import json
from collections import defaultdict

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing_extensions import Never

from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient

from agent_framework import (
    Agent,
    AgentResponse,
    Executor,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from agent_framework.observability import (
    configure_otel_providers,
    enable_sensitive_telemetry,
)

load_dotenv()
configure_otel_providers()
enable_sensitive_telemetry()


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
# Helpers
# ---------------------------------------------------------------------------

def _track_usage(tracker: dict, step: str, response: AgentResponse) -> None:
    usage = getattr(response, "usage_details", None) or {}
    bucket = tracker.setdefault(step, defaultdict(int))
    for key, value in usage.items():
        if isinstance(value, int):
            bucket[key] += value


def _extract_structured_text(response: AgentResponse) -> str:
    """Return only the text-typed content's JSON, skipping tool-call/result items.

    With MCP tools, ``response.messages`` may contain ``mcp_server_tool_call`` and
    ``mcp_server_tool_result`` items whose serialized form would otherwise be
    parsed before the real assistant JSON. We walk content items backwards and
    pick the last ``text`` item that contains a valid JSON object.
    """
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
    """Build a ``[system, user]`` message list.

    We pass ``instructions=`` via a system Message in the input (instead of via
    the ``options['instructions']`` slot) because the SDK silently drops the
    instructions option when a ``conversation_id`` is present, and we *do* want
    each agent's instructions injected on every one of its turns.
    """
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


# ---------------------------------------------------------------------------
# Custom executors (each one receives the shared conv_id and usage tracker)
# ---------------------------------------------------------------------------

class IntentStep(Executor):
    def __init__(
        self,
        agent: Agent,
        bu_id: int,
        conversation_id: str,
        usage_tracker: dict,
        id: str = "intent_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id
        self._conv_id = conversation_id
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
            options={
                "conversation_id": self._conv_id,
                "response_format": IntentResult,
            },
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
        conversation_id: str,
        usage_tracker: dict,
        id: str = "sql_builder_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._conv_id = conversation_id
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
            options={
                "conversation_id": self._conv_id,
                "response_format": SqlPlan,
            },
        )
        _track_usage(self._usage, "sql_builder", response)
        print("[DEBUG sql_builder messages]:")
        for i, m in enumerate(response.messages or []):
            for j, c in enumerate(getattr(m, "contents", None) or []):
                d = c.to_dict() if hasattr(c, "to_dict") else {}
                print(f"  msg[{i}].content[{j}] type={d.get('type')} preview={json.dumps(d, default=str)[:200]}")
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
        conversation_id: str,
        usage_tracker: dict,
        id: str = "query_executor_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._conv_id = conversation_id
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
        response = await self._agent.run(
            messages,
            options={"conversation_id": self._conv_id},
        )
        _track_usage(self._usage, "query_executor", response)
        await ctx.yield_output(AgentResponse(messages=response.messages))


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------

async def run_turn(
    user_question: str,
    bu_id: int,
    conversation_id: str,
    intent_agent: Agent,
    sql_builder_agent: Agent,
    query_executor_agent: Agent,
) -> tuple[AgentResponse | None, dict]:
    usage_tracker: dict = {}

    workflow = SequentialBuilder(
        participants=[
            IntentStep(intent_agent, bu_id=bu_id, conversation_id=conversation_id, usage_tracker=usage_tracker),
            SqlBuilderStep(sql_builder_agent, conversation_id=conversation_id, usage_tracker=usage_tracker),
            QueryExecutorStep(query_executor_agent, conversation_id=conversation_id, usage_tracker=usage_tracker),
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

        # --- Hosted MCP tool configs (one per agent role) ---------------------
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

        # --- Three client-side agents (no portal definitions) -----------------
        # Instructions are NOT set here; they are injected per call as a system
        # message so we can render the placeholders fresh each turn and so the
        # SDK does not skip them in the presence of a conversation_id.
        intent_agent = Agent(
            client=client,
            name="wfm-intent-classifier",
            tools=[mcp_list],
        )
        sql_builder_agent = Agent(
            client=client,
            name="wfm-sql-builder",
            tools=[mcp_schema],
        )
        query_executor_agent = Agent(
            client=client,
            name="wfm-query-executor",
            tools=[mcp_exec],
        )

        # --- Shared Foundry conversation across all three agents --------------
        async with AIProjectClient(endpoint=project_endpoint, credential=credential) as project:
            openai = project.get_openai_client()
            conversation = await openai.conversations.create()
            print(f"Foundry conversation: {conversation.id}")

        final, usage = await run_turn(
            user_question=user_question,
            bu_id=bu_id,
            conversation_id=conversation.id,
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
            label="variant D — FoundryChatClient, shared conv on ALL agents",
        )


if __name__ == "__main__":
    asyncio.run(main())
