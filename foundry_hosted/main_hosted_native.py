"""WFM Data Assistant — Foundry Hosted Agent (MULTITURN via native checkpoints).

Variant of ``main_hosted.py`` that uses **Foundry's native workflow checkpoint
persistence** (filesystem under ``/sessions/$HOME``) instead of Azure Cosmos
DB. Created as a parallel file so we can A/B-test the approach before
deciding which path ships.

Why this exists
---------------
Cosmos DB data-plane RBAC refuses the Foundry agent's identity (principal
type ``ServiceIdentity`` / ``microsoft.graph.agentIdentity``), and no
workaround exists in current API versions. Rather than introduce a new
storage backend (Blob, etc.), this variant rides on what Foundry already
gives us for free.

How Foundry persists workflow state
-----------------------------------
``ResponsesHostServer._handle_inner_workflow`` (SDK ``_responses.py``)
implements multi-turn workflow runs via **MAF workflow checkpoints** on a
``FileCheckpointStorage`` rooted at a directory keyed by the inbound
``context_id`` (``context.conversation_id`` when set, otherwise
``request.previous_response_id``). On each turn it:

1. Resolves ``context_id`` from the incoming request.
2. Restores the latest checkpoint for that key (if any), bringing back the
   workflow's shared state.
3. Runs the workflow with the new input.
4. Writes a fresh checkpoint to ``write_context_id``
   (``conversation_id`` when set, else current ``response_id``).

In the hosted environment the checkpoint directory lives under
``/sessions/$HOME``, the persistent filesystem of the Foundry session
(15 min idle deprovision → state preserved → restored on resume, 30 day max).

How we plug into it
-------------------
MAF Workflows expose a per-workflow shared ``State`` (key/value store) via
``WorkflowContext.get_state(key, default)`` / ``set_state(key, value)``.
The runner serialises that state into every checkpoint, so Foundry
restores it transparently across turns.

This variant:

1. Removes the Cosmos provider entirely (no ``CosmosHistoryProvider`` import,
   no session ContextVar, no custom response handler).
2. ``IntentStep`` reads prior history from
   ``ctx.get_state("history_messages", [])``, appends the new user turn,
   windows it, and feeds the classifier.
3. ``QueryExecutorStep`` appends ``(user, assistant)`` to the same key and
   writes it back via ``ctx.set_state(...)``. The runner picks it up at
   the superstep boundary; Foundry's host writes the checkpoint at the end
   of the run.
4. The ``recall_conversation`` function tool reads from a per-request
   ContextVar that ``QueryExecutorStep`` populates with the prior history
   snapshot just before invoking the executor agent.

Trade-offs vs the Cosmos variant
--------------------------------
- ✅ Zero new infrastructure, zero RBAC config — uses the platform's own
  persistence layer.
- ✅ Cross-channel: conversations created via Teams / Playground / API share
  the same ``conversation_id`` and thus the same checkpoint store.
- ✅ Foundry monitoring/eval can replay turns natively.
- ⚠️ Lifecycle is bounded by the session (max 30 days, 15 min idle resets the
  compute but state persists). No "lifetime" persistence.
- ⚠️ No cross-conversation querying or BU-wide search — the only way to
  retrieve a conversation is by its ``conversation_id``.

Run locally
-----------
::

    python main_hosted_native.py
    # in another terminal — multiturn via the Responses ``conversation`` field:
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
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.monitor.opentelemetry import configure_azure_monitor

load_dotenv()

# Telemetry bootstrap (same as the Cosmos variant — skip duplicate
# configuration when Foundry's hosted distro already set up the providers).
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

FOUNDRY_MODEL = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]
BU_ID = int(os.environ["BU_ID"])

# Sliding window passed to the classifier (turns = user/assistant pairs).
HISTORY_TURNS = 4
# Default depth of the recall_conversation tool.
RECALL_DEFAULT_TURNS = 10

# Workflow shared-state key under which we accumulate the conversation
# history. The MAF runner serialises this into every checkpoint; Foundry
# restores it across turns.
HISTORY_STATE_KEY = "history_messages"

# Per-request snapshot of the prior conversation, set by QueryExecutorStep
# right before running the executor agent. The recall_conversation tool
# reads from here (the workflow context isn't reachable from inside a
# function tool, so we bridge via a ContextVar instead).
_HISTORY_SNAPSHOT_CTX: contextvars.ContextVar[list[dict[str, str]] | None] = (
    contextvars.ContextVar("wfm_history_snapshot", default=None)
)


# ---------------------------------------------------------------------------
# Schemas (identical to the Cosmos variant)
# ---------------------------------------------------------------------------

class IntentResult(BaseModel):
    intent: str
    candidate_tables: list[str] = Field(default_factory=list)
    language_hint: str = "en"
    cache_action: str = "reuse"
    resolved_question: str = ""


class SqlPlan(BaseModel):
    sql: str = ""
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    explanation: str = ""
    error: str | None = None


class IntentBundle(BaseModel):
    user_question: str
    original_question: str
    bu_id: int
    intent_result: IntentResult


class SqlBundle(BaseModel):
    sql_plan: SqlPlan
    user_language: str
    user_question: str
    original_question: str


# ---------------------------------------------------------------------------
# Instructions (identical to the Cosmos variant)
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
# `recall_conversation` function tool
# ---------------------------------------------------------------------------

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
    """Return the last ``last_n_turns`` user turns plus their assistant replies.

    Reads from the per-request snapshot ContextVar populated by
    ``QueryExecutorStep`` immediately before invoking the executor agent.
    The snapshot is the prior history (i.e. excluding the current turn),
    matching the semantics of the Cosmos variant.
    """
    snapshot = _HISTORY_SNAPSHOT_CTX.get()
    if not snapshot:
        return "No prior conversation available."

    kept: list[dict[str, str]] = []
    user_count = 0
    for entry in reversed(snapshot):
        kept.append(entry)
        if entry.get("role") == "user":
            user_count += 1
            if user_count >= last_n_turns:
                break
    kept.reverse()

    lines: list[str] = []
    for entry in kept:
        role = entry.get("role", "?") or "?"
        text = (entry.get("text") or "").strip().replace("\n", " ")
        if len(text) > 800:
            text = text[:800] + "…"
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines) if lines else "Prior conversation is empty."


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


def _entries_to_messages(entries: list[dict[str, str]]) -> list[Message]:
    """Rehydrate the lightweight checkpoint format into ``Message`` objects.

    Workflow shared state must be JSON-serialisable for the runner to
    checkpoint it, so we persist plain ``{"role", "text"}`` dicts rather
    than full ``Message`` instances.
    """
    out: list[Message] = []
    for entry in entries:
        role = entry.get("role")
        text = entry.get("text") or ""
        if role and text:
            out.append(Message(role=role, contents=[text]))
    return out


def _windowed_history(history: list[Message], turns: int) -> list[Message]:
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
    """Intent classifier with checkpoint-backed history hydration.

    The hosting layer feeds this executor only the NEW user turn from the
    HTTP request. We hydrate prior turns from the workflow's shared state
    (which Foundry restores from the latest checkpoint), then window the
    resulting history and feed it to the classifier so coreference
    resolves correctly.
    """

    def __init__(
        self,
        agent: Agent,
        bu_id: int,
        id: str = "intent_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id

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

        # Prior history is whatever the last checkpoint left in shared state.
        # Empty list on the very first turn of a conversation.
        prior_entries: list[dict[str, str]] = list(
            ctx.get_state(HISTORY_STATE_KEY, []) or []
        )
        prior_messages = _entries_to_messages(prior_entries)

        history: list[Message] = list(prior_messages)
        # Defensive guard against double-appending the new user turn if it
        # were somehow already in state (it shouldn't be — QueryExecutorStep
        # is the only writer).
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
    """Final step: produce the answer and append the turn to shared state.

    Persistence happens HERE so the append is atomic with the workflow's
    successful output and naturally skipped when an earlier step raises.
    The MAF runner commits the updated state at the superstep boundary;
    Foundry's host then writes the checkpoint.
    """

    def __init__(
        self,
        agent: Agent,
        id: str = "query_executor_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent

    @handler
    async def run(
        self,
        bundle: SqlBundle,
        ctx: WorkflowContext[Never, AgentResponse],
    ) -> None:
        # Snapshot of the PRIOR history (without the current turn) so the
        # recall_conversation tool — called from inside self._agent.run()
        # below — can serve meta-questions.
        prior_entries: list[dict[str, str]] = list(
            ctx.get_state(HISTORY_STATE_KEY, []) or []
        )
        _HISTORY_SNAPSHOT_CTX.set(prior_entries)

        rendered = _render(
            QUERY_EXECUTOR_INSTRUCTIONS_TPL,
            sqlPlan=json.dumps(bundle.sql_plan.model_dump()),
            userLanguage=bundle.user_language,
        )
        messages = _build_messages(rendered, bundle.user_question)
        response = await self._agent.run(messages)

        # Append (raw user turn, assistant reply) to shared state. The runner
        # commits this at the superstep boundary; Foundry writes the
        # checkpoint at the end of the workflow run.
        if bundle.original_question:
            updated = list(prior_entries)
            updated.append({"role": "user", "text": bundle.original_question})
            for m in response.messages:
                text = (m.text or "").strip()
                if text:
                    updated.append({"role": "assistant", "text": text})
            ctx.set_state(HISTORY_STATE_KEY, updated)

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
            tools=[mcp_exec, recall_conversation],
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

        # No custom response handler needed: Foundry's
        # _handle_inner_workflow already keys checkpoints by
        # context.conversation_id (or request.previous_response_id) and
        # restores the shared State across turns automatically.
        server = ResponsesHostServer(workflow_agent)

        print(
            "Hosted agent ready on http://localhost:8088/responses "
            f"(bu_id={BU_ID}, model={FOUNDRY_MODEL}, "
            "persistence=native checkpoints). Ctrl-C to stop."
        )
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
