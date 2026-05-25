"""Sequential WFM workflow with a SHARED Foundry Conversation ONLY on the
agents whose conversational context actually matters (intent + executor).

Variant C (this file):
    - IntentStep        -> conversation_id = shared conv   (needs context to interpret follow-ups)
    - SqlBuilderStep    -> NO conversation_id              (operates only on structured_inputs)
    - QueryExecutorStep -> conversation_id = shared conv   (leaves natural-language trail
                                                            for the next turn / audit)

Pros: multi-turn works (intent sees prior user turns + executor answers),
      audit trail in Foundry portal preserved for what the USER perceives,
      sql-builder prompt stays lean (no token tax on intermediate JSON).

Cons: sql-builder's tool calls (getSchema) and SqlPlan JSON do NOT appear in the
      Foundry conversation. They are still fully traceable via OTEL trace_id
      (App Insights / Foundry Tracing), but NOT via the portal's conversation view.
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
    AgentResponse,
    Executor,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework.foundry import FoundryAgent
from agent_framework.orchestrations import SequentialBuilder
from agent_framework.observability import (
    configure_otel_providers,
    enable_sensitive_telemetry,
)

load_dotenv()
configure_otel_providers()
enable_sensitive_telemetry()


# ---------------------------------------------------------------------------
# Schemas mirroring the Foundry agent contracts
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
# Helpers
# ---------------------------------------------------------------------------

def _track_usage(tracker: dict, step: str, response) -> None:
    usage = getattr(response, "usage_details", None) or {}
    bucket = tracker.setdefault(step, defaultdict(int))
    for key, value in usage.items():
        if isinstance(value, int):
            bucket[key] += value


def _extract_structured_text(response) -> str:
    """Return only the text-typed content's JSON, skipping tool-call/result items."""
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
# Executors
# ---------------------------------------------------------------------------

class IntentStep(Executor):
    def __init__(
        self,
        agent: FoundryAgent,
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
        response = await self._agent.run(
            user_question,
            options={"conversation_id": self._conv_id},
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
    """No conversation_id — keeps the prompt lean."""

    def __init__(
        self,
        agent: FoundryAgent,
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
        structured_inputs = {
            "intentResult": bundle.intent_result.model_dump(),
            "buId": str(bundle.bu_id),
            "userQuestion": bundle.user_question,
        }
        response = await self._agent.run(
            bundle.user_question,
            options={"extra_body": {"structured_inputs": structured_inputs}},
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
        agent: FoundryAgent,
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
        structured_inputs = {
            "sqlPlan": bundle.sql_plan.model_dump(),
            "userLanguage": bundle.user_language,
        }
        response = await self._agent.run(
            bundle.user_question,
            options={
                # "conversation_id": self._conv_id,  # TEMP: disabled to test MCP-blocking hypothesis
                "extra_body": {"structured_inputs": structured_inputs},
            },
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
    intent_agent: FoundryAgent,
    sql_builder_agent: FoundryAgent,
    query_executor_agent: FoundryAgent,
) -> tuple[AgentResponse | None, dict]:
    usage_tracker: dict = {}

    workflow = SequentialBuilder(
        participants=[
            IntentStep(intent_agent, bu_id=bu_id, conversation_id=conversation_id, usage_tracker=usage_tracker),
            SqlBuilderStep(sql_builder_agent, usage_tracker=usage_tracker),
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

        async with AIProjectClient(endpoint=project_endpoint, credential=credential) as project:
            openai = project.get_openai_client()
            conversation = await openai.conversations.create()
            print(f"Foundry conversation: {conversation.id}")

        intent_agent = FoundryAgent(
            project_endpoint=project_endpoint,
            agent_name=os.environ["INTENT_AGENT_NAME"],
            credential=credential,
        )
        sql_builder_agent = FoundryAgent(
            project_endpoint=project_endpoint,
            agent_name=os.environ["SQL_BUILDER_AGENT_NAME"],
            credential=credential,
        )
        query_executor_agent = FoundryAgent(
            project_endpoint=project_endpoint,
            agent_name=os.environ["QUERY_EXECUTOR_AGENT_NAME"],
            credential=credential,
        )

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

        _print_usage_report(usage, label="variant C — shared conv on intent + executor only")


if __name__ == "__main__":
    asyncio.run(main())
