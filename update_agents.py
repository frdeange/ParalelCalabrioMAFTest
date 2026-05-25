"""Update the three WFM Foundry agents in one shot, enabling Structured
Outputs (json_schema) where it makes sense.

Run:
    python update_agents.py            # update all three
    python update_agents.py intent     # update only intent
    python update_agents.py sql        # update only sql-builder
    python update_agents.py executor   # update only query-executor

Requires (in .env):
    FOUNDRY_PROJECT_ENDPOINT
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentDefinition,
    MCPTool,
    MCPToolFilter,
    MCPToolRequireApproval,
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    StructuredInputDefinition,
    TextResponseFormatJsonSchema,
)

load_dotenv()

MODEL = "gpt-5.2"
MCP_SERVER_URL = "https://4x59q5fx-8001.uks1.devtunnels.ms/mcp/"


# ---------------------------------------------------------------------------
# JSON Schemas (Structured Outputs)
# ---------------------------------------------------------------------------

INTENT_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["DataQuery", "Conversational", "OutOfScope"],
        },
        "candidate_tables": {"type": "array", "items": {"type": "string"}},
        "language_hint": {"type": "string"},
        "cache_action": {"type": "string", "enum": ["reuse", "refresh"]},
    },
    "required": ["intent", "candidate_tables", "language_hint", "cache_action"],
}

SQL_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sql": {"type": "string"},
        "tables_used": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
        "error": {"type": ["string", "null"]},
    },
    "required": ["sql", "tables_used", "assumptions", "explanation", "error"],
}


# ---------------------------------------------------------------------------
# Instructions (system prompts)
# ---------------------------------------------------------------------------

INTENT_INSTRUCTIONS = """\
You are the Intent Classifier inside a controlled WFM data workflow.

Goal:
- Classify the user turn as exactly one of: DataQuery, Conversational, OutOfScope.
- Stay domain-neutral. Never rely on hidden business knowledge, hardcoded table names, or invented schema.

Inputs may include:
- user_message
- session_context: optional session state from the orchestrator

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

SQL_BUILDER_INSTRUCTIONS = """\
You are the SQL Builder inside a controlled WFM data workflow.

Mission:
- Convert the original user question into ONE safe SQL Server SELECT statement.
- Use ONLY the structured inputs supplied by the orchestrator and the metadata returned by MCP tools.
- Stay domain-neutral. The provided metadata is the only structural truth.

Structured inputs:
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

QUERY_EXECUTOR_INSTRUCTIONS = """\
You are the Query Executor and Formatter inside a controlled WFM data workflow.

Mission:
- Execute the SQL query from the plan using the executeQuery MCP tool.
- Produce the final user-facing answer from the execution results.
- Never invent facts. Speak only from the actual query results.
- Respond in the language indicated by `userLanguage` (BCP-47).

Structured inputs:
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mcp_tool(allowed: list[str]) -> MCPTool:
    return MCPTool(
        server_label="wfm-data",
        server_url=MCP_SERVER_URL,
        allowed_tools=MCPToolFilter(tool_names=allowed),
        require_approval=MCPToolRequireApproval(
            never=MCPToolFilter(tool_names=allowed),
        ),
    )


def _str_input(description: str = "") -> StructuredInputDefinition:
    return StructuredInputDefinition(
        description=description,
        required=True,
        schema={"type": "string"},
    )


def _json_input(description: str = "") -> StructuredInputDefinition:
    # Free-form JSON object — the agent template interpolates it via {{name}}.
    return StructuredInputDefinition(
        description=description,
        required=True,
        schema={"type": "object", "additionalProperties": True},
    )


# ---------------------------------------------------------------------------
# Agent factories
# ---------------------------------------------------------------------------

def intent_definition() -> PromptAgentDefinition:
    return PromptAgentDefinition(
        model=MODEL,
        instructions=INTENT_INSTRUCTIONS,
        tools=[_mcp_tool(["listTables"])],
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(
                name="IntentResult",
                strict=True,
                schema=INTENT_RESULT_SCHEMA,
            ),
        ),
    )


def sql_builder_definition() -> PromptAgentDefinition:
    return PromptAgentDefinition(
        model=MODEL,
        instructions=SQL_BUILDER_INSTRUCTIONS,
        tools=[_mcp_tool(["getSchema"])],
        structured_inputs={
            "intentResult": _json_input("Output object from the intent classifier."),
            "buId": _str_input("Business Unit identifier used as the mandatory scope filter."),
            "userQuestion": _str_input("Original natural-language question from the user."),
        },
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(
                name="SqlPlan",
                strict=True,
                schema=SQL_PLAN_SCHEMA,
            ),
        ),
    )


def query_executor_definition() -> PromptAgentDefinition:
    return PromptAgentDefinition(
        model=MODEL,
        instructions=QUERY_EXECUTOR_INSTRUCTIONS,
        tools=[_mcp_tool(["executeQuery"])],
        structured_inputs={
            "sqlPlan": _json_input("SqlPlan object produced by the SQL builder."),
            "userLanguage": _str_input("BCP-47 language code for the final answer."),
        },
        # No json_schema here: the executor returns natural language.
    )


AGENTS: dict[str, tuple[str, Callable[[], AgentDefinition]]] = {
    "intent": ("wfm-intent-classifier", intent_definition),
    "sql": ("wfm-sql-builder", sql_builder_definition),
    "executor": ("wfm-query-executor", query_executor_definition),
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    targets = args if args else list(AGENTS.keys())

    unknown = [t for t in targets if t not in AGENTS]
    if unknown:
        print(f"Unknown target(s): {unknown}. Valid: {list(AGENTS.keys())}")
        sys.exit(2)

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    credential = DefaultAzureCredential()

    with AIProjectClient(endpoint=endpoint, credential=credential) as client:
        for key in targets:
            agent_name, factory = AGENTS[key]
            print(f"Updating {agent_name} ...")
            new_version = client.agents.create_version(
                agent_name=agent_name,
                definition=factory(),
            )
            print(f"  -> {new_version.id} (version {new_version.version})")


if __name__ == "__main__":
    main()
