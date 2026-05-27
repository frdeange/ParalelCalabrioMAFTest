"""WFM Data Assistant — Foundry Hosted Agent (Responses protocol, MULTITURN).

What this is
------------
The same MAF workflow as ``main_local_multiturn.py`` (FoundryChatClient +
3 ``Agent`` instances + Local ``MCPStreamableHTTPTool`` + custom executors +
``CosmosHistoryProvider``), wrapped with ``ResponsesHostServer`` so it can be
deployed as a Foundry Hosted Agent on http://localhost:8088.

Multiturn + persistence design
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unlike the local REPL, the hosted server is request-driven. Each
``POST /responses`` brings only the new user turn — Foundry's hosting
layer does NOT prepend prior conversation messages to a workflow agent's
input (it manages declarative workflow state via checkpoints, which is
useless for our short single-shot workflow). Therefore we:

1. Receive the conversation/session identifier from the request via a
   custom response handler that wraps the framework's default. We use
   ``context.conversation_id`` (set when the caller passes a top-level
   ``conversation: "<id>"`` or ``conversation: {"id": "<id>"}`` per the
   Responses API), falling back to ``request.previous_response_id``.
   The identifier is stashed in a ContextVar that the workflow executors
   read.
2. ``IntentStep`` hydrates prior history from Cosmos using that session_id,
   appends the new user message, applies a sliding window, and feeds the
   classifier so coreference ("those", "y de esos") resolves correctly.
3. ``QueryExecutorStep`` persists the (user, assistant) pair to Cosmos
   after the final answer is produced. This matches the local REPL's
   "atomic save at end" semantics.
4. A ``recall_conversation`` function tool is wired ONLY to the executor;
   it reads the same Cosmos session via the ContextVar so the executor
   can answer meta-questions ("summarize what we discussed").

Sessions are OPTIONAL. If the caller does not send a ``conversation`` id,
the request runs single-turn (no hydration, no persistence). Persistent
multiturn requires the client to pass the same conversation id on every
call of a conversation.

Why a Hosted Agent (vs Foundry Prompt Agents)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Foundry Hosted Agents run the entire MAF workflow inside a container that
Foundry manages — your code IS the agent. This sidesteps every PromptAgent
constraint we hit during earlier experiments (no client tools, no per-call
``response_format``, missing first-agent telemetry) because the request is
served by our local code: there is no service-side agent runtime in the path.

Net effect:

- All 27+ workflow spans land in App Insights (same Local MCP path as the
  local variant).
- The workflow becomes addressable as an Agent via ``/responses``.
- ``azd deploy`` packages this code into a container and registers it as a
  hosted agent on the configured Foundry project.

Lifecycle
~~~~~~~~~
``ResponsesHostServer`` registers a ``shutdown_handler`` that calls the
agent's ``__aexit__`` (see ``_responses.py`` line ~365). Because
``WorkflowAgent`` inherits from ``BaseAgent`` (no async-context-manager
hooks), we cannot rely on the host to enter our MCP tools transitively
through the wrapper. Instead we open the MCP sessions ourselves via
``AsyncExitStack`` BEFORE ``await server.run_async()`` and let the stack
close on shutdown / Ctrl-C.

Run locally
-----------
::

    python main_hosted.py
    # then in another terminal — single-turn:
    curl -X POST http://localhost:8088/responses \\
      -H "Content-Type: application/json" \\
      -d '{"input": "\u00bfCu\u00e1ntos agentes hay en mi organizaci\u00f3n?"}'

    # multiturn — pass the same conversation id on every call:
    curl -X POST http://localhost:8088/responses \\
      -H "Content-Type: application/json" \\
      -d '{"input": "How many active agents in BU 1?", "conversation": "demo-001"}'
    curl -X POST http://localhost:8088/responses \\
      -H "Content-Type: application/json" \\
      -d '{"input": "and broken down by team?", "conversation": "demo-001"}'
"""

from __future__ import annotations

import os
import asyncio
import contextvars
import json
from contextlib import AsyncExitStack

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
    tool,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from agent_framework.observability import create_resource, enable_instrumentation
from agent_framework_azure_cosmos import CosmosHistoryProvider
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
# Configuration (fail-fast: every setting must come from the environment)
# ---------------------------------------------------------------------------

# Foundry's hosting platform injects AZURE_AI_MODEL_DEPLOYMENT_NAME for the
# model deployment. Local runs must also set it (no fallback — keep the
# environment surface uniform so configuration drift is loud).
FOUNDRY_MODEL = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]
# BU scope is a config concern for this experiment, not extracted from each
# inbound HTTP request. To make it per-call later, parse it out of the request
# in a custom Invocations handler or smuggle it via a custom header.
BU_ID = int(os.environ["BU_ID"])

# Cosmos DB NoSQL — backing store for conversation history. Authentication
# goes through Entra (DefaultAzureCredential) + Cosmos DB Built-in Data
# Contributor RBAC assigned to the Foundry project's managed identity.
COSMOS_ENDPOINT = os.environ["AZURE_COSMOS_ENDPOINT"]
COSMOS_DATABASE = os.environ["AZURE_COSMOS_DATABASE_NAME"]
COSMOS_CONTAINER = os.environ["AZURE_COSMOS_CONTAINER_NAME"]

# Sliding window for the conversation passed to the classifier.
# 4 turns ≈ 8 messages (user + assistant pairs). Enough for typical
# coreference cases without ballooning prompt tokens.
HISTORY_TURNS = 4

# Number of user/assistant turns the `recall_conversation` tool returns by
# default when invoked by the query executor.
RECALL_DEFAULT_TURNS = 10

# ContextVar holding the session_id of the current request. Set by the
# session-aware response handler before the workflow runs; read by
# IntentStep, QueryExecutorStep and the recall_conversation tool.
_SESSION_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "wfm_session_id", default=None
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IntentResult(BaseModel):
    intent: str
    candidate_tables: list[str] = Field(default_factory=list)
    language_hint: str = "en"
    cache_action: str = "reuse"
    # Standalone, conversation-independent restatement of the user's latest
    # turn. Required so downstream steps never need to look at conversation
    # history themselves.
    resolved_question: str = ""


class SqlPlan(BaseModel):
    sql: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    explanation: str = ""
    error: str | None = None


class IntentBundle(BaseModel):
    user_question: str          # resolved (standalone) — what downstream consumes
    original_question: str      # raw last user turn — kept for audit and persistence
    bu_id: int
    intent_result: IntentResult


class SqlBundle(BaseModel):
    sql_plan: SqlPlan
    user_language: str
    user_question: str          # same as IntentBundle.user_question (resolved)
    original_question: str      # forwarded so the executor can persist the raw user turn


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

INTENT_INSTRUCTIONS_TPL = """\
You are the Intent Classifier inside a controlled WFM data workflow.

You receive the full recent conversation history (system instructions, prior
user turns, prior assistant turns). Your job is to look at the LATEST user
turn and produce a structured classification AND a standalone restatement of
that latest user turn.

Goal:
- Classify the latest user turn as exactly one of: DataQuery, Conversational, OutOfScope.
- Produce `resolved_question`: a self-contained restatement of the latest user
  turn that does NOT depend on prior conversation to be understood.
- Stay domain-neutral. Never rely on hidden business knowledge, hardcoded
  table names, or invented schema.

Rules:
1. Choose DataQuery only when the user needs live data, counts, filters,
   trends, records, or verification from the database.
2. Choose Conversational for greetings, clarifications, help text, or
   general discussion that does not require live data.
3. Choose OutOfScope for requests outside the approved WFM data assistant
   scope, unsafe asks, or requests the workflow cannot satisfy.
4. For DataQuery, call the listTables MCP tool first and populate
   candidate_tables only with tables it returns. Never invent table names.
5. Detect the language of the latest user message and set language_hint to
   its BCP-47 code (e.g. "en", "es"). The user may write in any language.
6. Set cache_action to "reuse" by default; use "refresh" only when the user
   explicitly asks for fresh data or signals the cached answer is stale.
7. For Conversational or OutOfScope turns, return candidate_tables as an
   empty array.
8. Never invent columns, joins, or business rules.

Rules for `resolved_question`:
A. If the latest user turn references prior turns ("those", "them", "the
   previous result", "y de esos…", "and how many of them are active?",
   "and broken down by team?"), REWRITE it into a fully standalone question
   that:
   - Substitutes pronouns and demonstratives with the explicit entities they
     refer to, taken from the prior turns.
   - Carries forward implicit subjects, filters and constraints from earlier
     turns when the new turn is clearly a follow-up.
   - Preserves the language of the latest user turn.
   - Does NOT invent facts. If the prior context is insufficient to make the
     reference unambiguous, leave the latest user turn unchanged and let the
     downstream steps surface the ambiguity.
B. If the latest user turn is already self-contained, copy it verbatim into
   `resolved_question`.
C. Keep `resolved_question` short and natural — it is the input that the
   SQL builder will see; it must read like a single, well-formed question.
"""

SQL_BUILDER_INSTRUCTIONS_TPL = """\
You are the SQL Builder inside a controlled WFM data workflow.

Mission:
- Convert the user question into ONE safe SQL Server SELECT statement.
- Use ONLY the structured inputs supplied here and the metadata returned by MCP tools.
- Stay domain-neutral. The provided metadata is the only structural truth.

Inputs for this turn:
- intentResult: {{intentResult}}
- buId: {{buId}}
- userQuestion: {{userQuestion}}

The userQuestion is already a standalone, conversation-independent question.
You do NOT need to (and must NOT) consider any prior conversation context.

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

Available tools:
- executeQuery (MCP): run the SQL from `sqlPlan.sql`.
- recall_conversation (function): retrieve the recent conversation history
  between you and the user. Use ONLY when the user is asking a META question
  about the conversation itself — e.g. "summarize what we discussed",
  "resume what we talked about", "what did I ask first", "compare this with
  the previous result", "how does this change vs before". DO NOT use it for
  normal data queries; normal queries are answered from executeQuery rows.

Rules:
1. If `sqlPlan.error` is not null or `sqlPlan.sql` is empty, do NOT call executeQuery. In that case:
   a. If the user's latest message is a meta-question about the conversation (see recall_conversation tool description), call recall_conversation, then answer from its output.
   b. Otherwise, give a short, non-technical message stating the request could not be processed and, when useful, hint at what additional info would help.
2. Otherwise, call executeQuery with `sqlPlan.sql` and treat the returned rows as the only source of truth.
3. If the query returns 0 rows, state clearly that no matching records were found in the allowed scope.
4. Summarize key counts, trends, or highlights that are directly supported by the rows. Be concise, accurate, and helpful.
5. If execution fails, give a short non-technical recovery message. Never expose SQL, stack traces, or internal identifiers.
6. Never claim the query ran if the execution result is missing or failed.
7. Output plain natural language only. No markdown tables unless the user explicitly requested tabular output.
8. Never expose session identifiers, tool names, or internal mechanics in your answer.
"""


def _render(template: str, **vars: object) -> str:
    out = template
    for key, value in vars.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


# ---------------------------------------------------------------------------
# `recall_conversation` function tool (wired ONLY to the query executor)
# ---------------------------------------------------------------------------

def make_recall_tool(provider: CosmosHistoryProvider):
    """Build the ``recall_conversation`` function tool bound to *provider*.

    The tool reads the current session_id from a module-level ContextVar
    (set by the session-aware response handler before the workflow runs),
    fetches the messages stored in Cosmos for that session, and returns a
    compact text rendering of the last ``last_n_turns`` user/assistant
    exchanges. The query executor agent decides when to call it based on
    its system instructions.
    """

    @tool(
        name="recall_conversation",
        description=(
            "Retrieve the recent conversation history between you and the user. "
            "Returns the last N user/assistant turns as plain text. "
            "Use ONLY when the user is asking a meta-question about the conversation "
            "itself (e.g. 'summarize what we discussed', 'resume what we talked about', "
            "'what did I ask first', 'compare with the previous result'). "
            "Do NOT use for normal data queries."
        ),
    )
    async def recall_conversation(last_n_turns: int = RECALL_DEFAULT_TURNS) -> str:
        sid = _SESSION_ID_CTX.get()
        if not sid:
            return "No active conversation session."
        messages = await provider.get_messages(sid)
        if not messages:
            return "Conversation is empty."
        kept: list[Message] = []
        user_count = 0
        for m in reversed(messages):
            kept.append(m)
            if getattr(m, "role", None) == "user":
                user_count += 1
                if user_count >= last_n_turns:
                    break
        kept.reverse()
        lines: list[str] = []
        for m in kept:
            role = getattr(m, "role", "?") or "?"
            text = (m.text or "").strip().replace("\n", " ")
            if len(text) > 800:
                text = text[:800] + "…"
            if text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines) if lines else "Conversation is empty."

    return recall_conversation


# ---------------------------------------------------------------------------
# Helpers
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


def _windowed_history(history: list[Message], turns: int) -> list[Message]:
    """Return the last `turns` user/assistant pairs of the history.

    A "turn" here is one user message plus its corresponding assistant
    reply. We keep the very last user message even if turns=0 so the
    classifier always has at least the current question.
    """
    if turns <= 0 or not history:
        for i in range(len(history) - 1, -1, -1):
            if history[i].role == "user":
                return [history[i]]
        return list(history)

    kept: list[Message] = []
    user_count = 0
    for m in reversed(history):
        kept.append(m)
        if m.role == "user":
            user_count += 1
            if user_count > turns:
                kept.pop()
                break
    return list(reversed(kept))


# ---------------------------------------------------------------------------
# Custom executors
# ---------------------------------------------------------------------------

class IntentStep(Executor):
    """Intent classifier with Cosmos-backed history hydration.

    The hosting layer feeds this executor only the NEW user turn from the
    HTTP request (workflow agents do not get prior messages prepended).
    We hydrate prior turns from Cosmos using the session_id stashed in the
    ContextVar by the response handler, then window the resulting history
    and feed it to the classifier so coreference resolves correctly.
    """

    def __init__(
        self,
        agent: Agent,
        bu_id: int,
        history_provider: CosmosHistoryProvider,
        id: str = "intent_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id
        self._provider = history_provider

    @handler
    async def run(
        self,
        conversation: list[Message],
        ctx: WorkflowContext[IntentBundle],
    ) -> None:
        original_question = next(
            (m.text for m in reversed(conversation) if m.role == "user" and m.text),
            "",
        )

        sid = _SESSION_ID_CTX.get()
        prior: list[Message] = []
        if sid:
            try:
                prior = list(await self._provider.get_messages(sid))
            except Exception as exc:  # noqa: BLE001 — never break a turn on history load
                print(f"  [warn] failed to load history from Cosmos: {exc}")
                prior = []

        # Build the full working history: prior turns from Cosmos + the new
        # user turn from this request. Avoid double-counting if the new
        # user message somehow already exists in Cosmos (it shouldn't, but
        # be defensive).
        history: list[Message] = list(prior)
        if not (
            history
            and history[-1].role == "user"
            and (history[-1].text or "").strip() == (original_question or "").strip()
        ):
            history.extend(conversation)

        windowed = _windowed_history(history, HISTORY_TURNS)
        prompt_messages: list[Message] = [
            Message(role="system", contents=[INTENT_INSTRUCTIONS_TPL]),
            *windowed,
        ]

        response = await self._agent.run(
            prompt_messages,
            options={"response_format": IntentResult},
        )
        intent = IntentResult.model_validate_json(_extract_structured_text(response))
        resolved = intent.resolved_question.strip() or original_question

        await ctx.send_message(
            IntentBundle(
                user_question=resolved,
                original_question=original_question,
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
                original_question=bundle.original_question,
            )
        )


class QueryExecutorStep(Executor):
    """Final step: produce the answer and persist the turn to Cosmos.

    Persistence happens HERE (not in the response handler wrapper) so the
    save is atomic with the workflow's successful output and naturally
    skipped when an earlier step raises. The (raw user, assistant) pair
    is stored under the active session_id.
    """

    def __init__(
        self,
        agent: Agent,
        history_provider: CosmosHistoryProvider,
        id: str = "query_executor_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._provider = history_provider

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

        # Persist (raw user turn, assistant reply) under the active session.
        # Only when the caller supplied a conversation id — single-shot
        # requests intentionally leave no trace.
        sid = _SESSION_ID_CTX.get()
        if sid and bundle.original_question:
            user_msg = Message(role="user", contents=[bundle.original_question])
            assistant_msgs: list[Message] = []
            for m in response.messages:
                text = (m.text or "").strip()
                if text:
                    assistant_msgs.append(Message(role="assistant", contents=[text]))
            try:
                await self._provider.save_messages(
                    sid, [user_msg, *assistant_msgs]
                )
            except Exception as exc:  # noqa: BLE001 — never break a turn on persistence
                print(f"  [warn] failed to persist turn to Cosmos: {exc}")

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

        # Persistent conversation history backed by Cosmos DB NoSQL.
        # Owned by the orchestrator; the agents themselves are NOT wired
        # to it as context_providers. Authentication via Entra
        # (DefaultAzureCredential) + Cosmos DB Built-in Data Contributor
        # RBAC on the Foundry project's managed identity.
        history_provider = CosmosHistoryProvider(
            endpoint=COSMOS_ENDPOINT,
            database_name=COSMOS_DATABASE,
            container_name=COSMOS_CONTAINER,
            credential=credential,
            load_messages=False,   # IntentStep loads explicitly
            store_inputs=False,    # QueryExecutorStep persists via save_messages
            store_outputs=False,   # QueryExecutorStep persists via save_messages
        )

        # Function tool for on-demand conversation recall. Wired ONLY to
        # the executor (intent agent already sees windowed history; sql
        # builder operates on a standalone resolved question).
        recall_tool = make_recall_tool(history_provider)

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
            tools=[mcp_exec, recall_tool],
        )

        workflow = SequentialBuilder(
            participants=[
                IntentStep(intent_agent, bu_id=BU_ID, history_provider=history_provider),
                SqlBuilderStep(sql_builder_agent),
                QueryExecutorStep(query_executor_agent, history_provider=history_provider),
            ]
        ).build()

        workflow_agent = workflow.as_agent(
            name="wfm-data-assistant",
            description="WFM data assistant: classifies intent, builds a "
            "scoped SQL query, executes it, and answers in the user's language.",
        )

        server = ResponsesHostServer(workflow_agent)

        # Wrap the framework's default response handler with one that
        # extracts the conversation id from the request context and sets
        # our ContextVar. Source of truth (in order):
        #   1. ``context.conversation_id``    — Responses API ``conversation`` field.
        #   2. ``request.previous_response_id`` — chained calls without an explicit
        #      conversation id; uses the prior response id as the session key.
        # When neither is present the request runs single-turn (no
        # hydration, no persistence) — sid stays ``None``.
        framework_handler = server._handle_response  # type: ignore[attr-defined]

        @server.response_handler
        async def session_aware_handler(request, context, cancellation_signal):
            sid = context.conversation_id or request.previous_response_id
            if sid:
                _SESSION_ID_CTX.set(sid)
            iterable = await framework_handler(request, context, cancellation_signal)
            async for event in iterable:
                yield event

        print(
            "Hosted agent ready on http://localhost:8088/responses "
            f"(bu_id={BU_ID}, model={FOUNDRY_MODEL}, "
            f"cosmos={COSMOS_DATABASE}/{COSMOS_CONTAINER}). Ctrl-C to stop."
        )
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
