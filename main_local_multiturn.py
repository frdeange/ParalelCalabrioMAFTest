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
from contextlib import AsyncExitStack
from uuid import uuid4

from dotenv import load_dotenv

from azure.identity.aio import DefaultAzureCredential
from agent_framework import (
    Agent,
    AgentResponse,
    MCPStreamableHTTPTool,
    Message,
    tool,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework.observability import create_resource, enable_instrumentation
from agent_framework_azure_cosmos import CosmosHistoryProvider
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace, metrics

# Phase-0 quickstart compatibility: when this script is run with only the
# top-level ``requirements.txt`` installed (no ``pip install -e apps/backend``),
# the ``app`` package is not importable. Prepend ``apps/backend`` to
# ``sys.path`` so ``from app.workflow import ...`` resolves either way.
# Once Phase 1 lands the AG-UI FastAPI entrypoint (issue #12), this REPL
# script becomes a development convenience and the production path uses
# the installed ``wfm-backend`` package.
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent / "apps" / "backend"
if _BACKEND_DIR.is_dir() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Workflow Executors, schemas and assembly live in the installed
# ``wfm-backend`` package (see ``apps/backend/app/workflow``). This module
# keeps only the REPL glue, the agent factories and the recall_conversation
# tool wiring; the three Executors are pure code-moved.
from app.workflow import HISTORY_TURNS, IntentResult, build_workflow  # noqa: E402

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


# Schemas, prompt templates, helper utilities and the three Executors used
# to live here as a single flat module. They moved to ``app.workflow``
# (issue #8) so they can be unit-tested and reused by the FastAPI app. Only
# ``IntentResult`` is re-imported above for the REPL's intent summary.


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

        workflow = build_workflow(
            intent_agent=intent_agent,
            sql_builder_agent=sql_builder_agent,
            query_executor_agent=query_executor_agent,
            bu_id=BU_ID,
            usage_tracker=cumulative_usage,
        )

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

        # MCP wiring (PLAN.md decision D10):
        # - intent_agent has NO tools — it only classifies + resolves the
        #   question from the conversation.
        # - sql_builder_agent owns both ``listTables`` and ``getSchema`` so
        #   it can discover the catalog and the relevant schemas on its own.
        # - query_executor_agent owns ``executeQuery`` plus the in-process
        #   ``recall_conversation`` function tool.
        mcp_schema = await mcp_stack.enter_async_context(
            MCPStreamableHTTPTool(
                name="wfm-data",
                url=MCP_SERVER_URL,
                allowed_tools=["listTables", "getSchema"],
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
