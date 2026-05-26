"""WFM Data Assistant — Local + MULTITURN variant (REPL).

What changes vs ``main_local.py``
---------------------------------
This file exists to validate the **Level 1 multiturn fix** plus a **persistent
conversation backend** without touching the production ``main_local.py`` or
``foundry_hosted/main_hosted.py`` paths. It is the same MAF
``SequentialBuilder`` (intent → sql → executor) with three orthogonal changes:

1. **`resolved_question` on `IntentResult`.** The intent classifier rewrites
   pronoun- or context-dependent latest user turns into a fully standalone
   question. Downstream steps consume that resolved question and never look
   at history themselves — they don't need to.

2. **Persistent conversation history in Azure Cosmos DB.** A single
   ``CosmosHistoryProvider`` instance is owned by the REPL/orchestrator and
   used as a plain key-value store keyed by ``session_id``. On REPL start
   the history is hydrated from Cosmos; after every turn the (user,
   assistant) pair is appended. This means:
     - Multi-instance Hosted Agent replicas all see the same conversation.
     - Process restarts / revisions do not lose the conversation.
     - The audit trail is durable for compliance.
   The agents themselves are NOT wired to the provider — we control history
   flow explicitly to keep the windowing predictable.

3. **An on-demand `recall_conversation` function tool.** Wired ONLY to the
   ``query_executor_agent``. It lets the executor read prior turns when the
   user asks meta-questions ("resume what we discussed", "compare with
   before"), since the executor by design only receives the SQL plan plus
   user language — it has no conversation context otherwise. The tool reads
   from the same ``CosmosHistoryProvider`` using a ``ContextVar``-scoped
   ``session_id`` set at REPL turn entry.

Why this design (vs a separate rewriter Executor or per-agent history)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The intent classifier is already an LLM call. Adding one more field to its
``response_format`` schema is free in latency, cost and tracing surface.
Attaching ``CosmosHistoryProvider`` to all three agents would persist every
intermediate agent input/output (3× per turn) which is noisy and not what
we want as "conversation". Keeping persistence at the orchestrator level
means Cosmos stores exactly what the user sees.

Run
---
::

    python main_local_multiturn.py

Then type questions one per line. Useful commands:
- ``exit`` / ``quit`` / ``q``  → leave
- ``reset``                    → start a brand new session (new session_id)
- ``history``                  → dump accumulated messages
- ``session``                  → print current session_id
- empty line                   → noop
"""

from __future__ import annotations

import os
import asyncio
import contextvars
import json
from collections import defaultdict
from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

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
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace, metrics

load_dotenv()

configure_azure_monitor(
    connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
    resource=create_resource(),
    enable_live_metrics=True,
)
enable_instrumentation(enable_sensitive_data=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOUNDRY_MODEL = os.environ["FOUNDRY_DEPLOYMENT_NAME"]
MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]
BU_ID = int(os.environ.get("BU_ID", "1"))

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

# ContextVar holding the session_id of the current REPL/Hosted-Agent
# conversation. Set at the entry of each turn. Used by the
# `recall_conversation` function tool so it can look up the right rows in
# Cosmos without needing to be a closure over mutable state.
_SESSION_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "wfm_session_id", default=None
)


# ---------------------------------------------------------------------------
# Schemas — IntentResult gains `resolved_question`
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
    original_question: str      # raw last user turn — kept for audit only
    bu_id: int
    intent_result: IntentResult


class SqlBundle(BaseModel):
    sql_plan: SqlPlan
    user_language: str
    user_question: str          # same as IntentBundle.user_question (resolved)


# ---------------------------------------------------------------------------
# Instructions — only the intent classifier instructions change
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
# Intent rules — NEW: SqlBuilder must explicitly fail for meta-queries
# ---------------------------------------------------------------------------
# The SQL builder will receive Conversational/OutOfScope intents too (the
# SequentialBuilder runs every step). For those, we want it to produce an
# empty SqlPlan with `error` set, so the executor falls through to either
# recall_conversation (meta-query) or a polite refusal.


# ---------------------------------------------------------------------------
# `recall_conversation` function tool (wired ONLY to the query executor)
# ---------------------------------------------------------------------------

def make_recall_tool(provider: CosmosHistoryProvider):
    """Build the `recall_conversation` function tool bound to *provider*.

    The tool reads the current session_id from a module-level ContextVar,
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
        # Walk from the end, keeping at most `last_n_turns` user messages
        # and their accompanying assistant replies.
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

def _track_usage(tracker: dict, step: str, response: AgentResponse) -> None:
    usage = getattr(response, "usage_details", None) or {}
    bucket = tracker.setdefault(step, defaultdict(int))
    for key, value in usage.items():
        if isinstance(value, int):
            bucket[key] += value


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
        # Always keep at least the last user message
        for i in range(len(history) - 1, -1, -1):
            if history[i].role == "user":
                return [history[i]]
        return list(history)

    # Walk back, counting user messages until we've collected `turns + 1`
    # of them (turns prior + the latest one).
    kept: list[Message] = []
    user_count = 0
    for m in reversed(history):
        kept.append(m)
        if m.role == "user":
            user_count += 1
            if user_count > turns:
                kept.pop()  # we went one too far
                break
    return list(reversed(kept))


# ---------------------------------------------------------------------------
# Custom executors
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
        # Original latest user message — kept for the audit bundle.
        original_question = next(
            (m.text for m in reversed(conversation) if m.role == "user" and m.text),
            "",
        )

        # Build the prompt: system instructions + the windowed conversation.
        # Note: we pass the conversation as actual `user`/`assistant`
        # messages so the model sees the natural dialog format. This is
        # better than serializing it into a string blob.
        windowed = _windowed_history(conversation, HISTORY_TURNS)
        prompt_messages: list[Message] = [
            Message(role="system", contents=[INTENT_INSTRUCTIONS_TPL]),
            *windowed,
        ]

        response = await self._agent.run(
            prompt_messages,
            options={"response_format": IntentResult},
        )
        _track_usage(self._usage, "intent", response)
        intent = IntentResult.model_validate_json(_extract_structured_text(response))

        # Fallback: if the model returned an empty resolved_question for any
        # reason, fall back to the raw latest user turn.
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
            userQuestion=bundle.user_question,  # already resolved
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
# REPL
# ---------------------------------------------------------------------------

BANNER = """
================================================================
 WFM Data Assistant — MULTITURN REPL (Cosmos-backed + App Insights)
================================================================
 model         = {model}
 bu_id         = {bu_id}
 history win.  = {turns} prior user turns
 cosmos db     = {db}/{cont}
 session_id    = {sid}
 commands: exit | quit | q | reset | history | session
================================================================
""".strip()


def _print_intent_summary(intent: IntentResult, original: str) -> None:
    if intent.resolved_question and intent.resolved_question.strip() != original.strip():
        print(f"  [intent] resolved → {intent.resolved_question!r}")
    print(
        f"  [intent] kind={intent.intent} "
        f"tables={intent.candidate_tables} "
        f"lang={intent.language_hint} cache={intent.cache_action}"
    )


def _print_history(history: list[Message]) -> None:
    if not history:
        print("  (empty)")
        return
    for i, m in enumerate(history):
        text = (m.text or "").strip().replace("\n", " ")
        if len(text) > 160:
            text = text[:160] + "…"
        print(f"  [{i:02d}] {m.role}: {text}")


async def repl(
    intent_agent: Agent,
    sql_builder_agent: Agent,
    query_executor_agent: Agent,
    history_provider: CosmosHistoryProvider,
) -> None:
    session_id = str(uuid4())
    _SESSION_ID_CTX.set(session_id)

    # Hydrate local working copy from Cosmos. For a brand-new REPL session
    # this is empty; if you pass a known session_id from outside (e.g. via
    # an env var — not implemented here) you'd resume a previous chat.
    history: list[Message] = list(await history_provider.get_messages(session_id))
    cumulative_usage: dict = {}

    print(
        BANNER.format(
            model=FOUNDRY_MODEL,
            bu_id=BU_ID,
            turns=HISTORY_TURNS,
            db=COSMOS_DATABASE,
            cont=COSMOS_CONTAINER,
            sid=session_id,
        )
    )
    if history:
        print(f"  (resumed {len(history)} prior messages from Cosmos)")

    loop = asyncio.get_running_loop()

    while True:
        try:
            line = await loop.run_in_executor(None, input, "\n>>> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return
        cmd = line.strip()
        if not cmd:
            continue
        low = cmd.lower()
        if low in {"exit", "quit", "q"}:
            print("bye.")
            return
        if low == "reset":
            # New session_id → prior conversation is preserved in Cosmos
            # under the old session_id, but the new one starts empty.
            session_id = str(uuid4())
            _SESSION_ID_CTX.set(session_id)
            history.clear()
            cumulative_usage.clear()
            print(f"  (new session: {session_id})")
            continue
        if low == "history":
            _print_history(history)
            continue
        if low == "session":
            print(f"  session_id = {session_id}")
            continue

        # Make sure the ContextVar is set for this turn (covers the case
        # where the asyncio task that calls into the recall tool inherits
        # the right value).
        _SESSION_ID_CTX.set(session_id)

        # Append latest user message and run the workflow on the full
        # accumulated history (windowing happens inside IntentStep).
        user_msg = Message(role="user", contents=[cmd])
        history.append(user_msg)

        workflow = SequentialBuilder(
            participants=[
                IntentStep(intent_agent, bu_id=BU_ID, usage_tracker=cumulative_usage),
                SqlBuilderStep(sql_builder_agent, usage_tracker=cumulative_usage),
                QueryExecutorStep(query_executor_agent, usage_tracker=cumulative_usage),
            ]
        ).build()

        try:
            events = await workflow.run(list(history))
        except Exception as exc:  # noqa: BLE001 — REPL must not die on a bad turn
            print(f"  [error] workflow failed: {exc}")
            # Roll back the user turn so the next attempt re-tries fresh.
            history.pop()
            continue

        outputs = events.get_outputs()
        if not outputs:
            print("  (no output)")
            history.pop()
            continue

        final: AgentResponse = outputs[0]

        # Print the final answer, append it to local history, and persist
        # the (user, assistant) pair to Cosmos.
        assistant_messages: list[Message] = []
        for msg in final.messages:
            author = msg.author_name or "assistant"
            text = (msg.text or "").strip()
            if not text:
                continue
            print(f"\n[{author}]\n{text}")
            assistant_msg = Message(role="assistant", contents=[text])
            history.append(assistant_msg)
            assistant_messages.append(assistant_msg)

        try:
            await history_provider.save_messages(
                session_id, [user_msg, *assistant_messages]
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] failed to persist turn to Cosmos: {exc}")


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------

async def main() -> None:
    async with DefaultAzureCredential() as credential, AsyncExitStack() as mcp_stack:
        project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]

        client = FoundryChatClient(
            project_endpoint=project_endpoint,
            model=FOUNDRY_MODEL,
            credential=credential,
        )

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
        # Owned by the orchestrator; the agents themselves are not wired
        # to it. Authentication via Entra (DefaultAzureCredential) +
        # Cosmos DB Built-in Data Contributor RBAC.
        history_provider = CosmosHistoryProvider(
            endpoint=COSMOS_ENDPOINT,
            database_name=COSMOS_DATABASE,
            container_name=COSMOS_CONTAINER,
            credential=credential,
            load_messages=False,   # we feed history to agents explicitly
            store_inputs=False,    # orchestrator persists via save_messages
            store_outputs=False,   # orchestrator persists via save_messages
        )

        # Function tool for on-demand conversation recall. Wired ONLY
        # to the executor (intent agent already sees history, sql builder
        # operates on a standalone resolved question).
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

        await repl(
            intent_agent,
            sql_builder_agent,
            query_executor_agent,
            history_provider,
        )

    # Flush exporters on exit
    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "force_flush"):
        tracer_provider.force_flush(timeout_millis=10_000)
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "force_flush"):
        meter_provider.force_flush(timeout_millis=10_000)


if __name__ == "__main__":
    asyncio.run(main())
