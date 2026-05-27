# WFM Sequential Workflow — Microsoft Agent Framework + Azure AI Foundry

> Validation project for a multi-agent **Sequential Orchestration** pipeline built on top of
> [Microsoft Agent Framework (MAF)](https://learn.microsoft.com/agent-framework/) and
> **Azure AI Foundry** Prompt Agents, using **server-side Structured Outputs (JSON Schema)**,
> **structured inputs**, **MCP tools**, and **OpenTelemetry**.
>
> The pipeline answers natural-language questions about a Workforce Management (WFM) database by
> chaining three Foundry agents: intent → SQL plan → SQL execution + natural answer.

---

## 0. Audience for this document

This README is written for **another AI agent / engineer** that needs to fully understand:

1. The end-to-end architecture and runtime data flow.
2. How **Structured Outputs** are configured **server-side** on each Foundry agent (not via system prompt).
3. How **Structured Inputs** are declared on the agent and **how they are passed at call time**.
4. How **custom MAF `Executor`s** bridge `pydantic` models from one agent to the next.
5. How the **agent updates** are managed in code (idempotent, versioned, single script).
6. How **MCP tools** are bound to each agent and approval semantics.
7. How **telemetry** is wired (OTLP / Application Insights).
8. The exact contracts (JSON Schemas) every agent must satisfy.

Read sections **1 → 7** in order. Section **8** is a reference of fixes/pitfalls.

---

## 1. Repository layout

```
ParalelCalabrioMAFTest/
├── main_local.py             # Local MAF orchestrator — single-turn (Foundry LLM + local MCP)
├── main_local_multiturn.py   # Local MAF orchestrator — multi-turn REPL with Cosmos DB history
├── update_agents.py          # Idempotent script that (re)publishes the 3 Foundry Prompt Agents
├── promptAgents/             # Reference YAMLs for the 3 agents (documentation only)
│   ├── intent-classifier.yaml
│   ├── sql-builder.yaml
│   └── query-executor.yaml
├── requirements.txt
├── azure.yaml                # azd manifest (services: foundry_hosted as a Foundry Hosted Agent)
├── .env.example              # Sample env file (real .env is gitignored)
├── foundry_hosted/           # Foundry Hosted Agent variants (deployed via `azd deploy`)
│   ├── main_hosted.py        # Hosted variant backed by Cosmos DB history (blocked — see § 17)
│   ├── main_hosted_native.py # Hosted variant backed by Foundry native checkpoints (ACTIVE — v9)
│   ├── Dockerfile            # python:3.13-slim; CMD points to main_hosted_native.py
│   ├── requirements.txt      # Pinned deps installed inside the container
│   ├── agent.yaml            # Runtime config consumed by `azd ai agent run`/`azd deploy`
│   ├── agent.manifest.yaml   # Consumed by `azd ai agent init` to scaffold the project
│   └── .dockerignore
└── scripts/                  # Operational scripts for the Hosted Agent (see § 16)
    ├── preflight-check.sh    # Pre-deploy validation: CLI, env, MI, RBAC, MCP
    └── deploy-hosted-agent.sh # End-to-end deploy: preflight + RBAC fix + azd deploy + smoke test
```

Three runtime entry points share the **same workflow** (intent → SQL builder → query executor):

- `main_local.py` — single-turn local REPL; useful for quick development and smoke-testing.
- `main_local_multiturn.py` — multi-turn local REPL with Cosmos DB-backed conversation history.
  **This is the production-ready local variant** (see § 17 for current project status).
- `foundry_hosted/main_hosted_native.py` — same workflow wrapped in `ResponsesHostServer` with
  multi-turn backed by **Foundry native workflow checkpoints** (no external storage required).
  Currently deployed as Foundry Hosted Agent v9. Functional but with known observability
  limitations (see § 17).

`foundry_hosted/main_hosted.py` is kept as a reference/rollback: it uses `CosmosHistoryProvider`
for history but cannot run in the hosted environment due to a platform identity gap (see § 17.2).

`promptAgents/*.yaml` are **only historical/reference docs** of the prompt content. The actual
source of truth for the deployed Foundry Prompt Agents is `update_agents.py` (instructions,
JSON Schemas and structured-input declarations are embedded there as Python constants).

---

## 2. High-level architecture

```
                       ┌──────────────────────────────────────┐
 user_question ───►    │            main_local.py              │
 (str)                 │   SequentialBuilder workflow         │
                       │                                      │
                       │  ┌────────────┐    IntentBundle      │
                       │  │ IntentStep │ ───────────────────► │
                       │  └─────┬──────┘                      │
                       │        │ run(intent_agent)           │
                       │        ▼                             │
                       │  Foundry agent:                      │
                       │   wfm-intent-classifier              │
                       │   • MCP tool: listTables             │
                       │   • text.format = json_schema        │
                       │     (IntentResult, strict=True)      │
                       │                                      │
                       │  ┌─────────────────┐  SqlBundle      │
                       │  │ SqlBuilderStep  │ ──────────────► │
                       │  └────────┬────────┘                 │
                       │           │ run(sql_builder_agent)   │
                       │           │ structured_inputs:       │
                       │           │   intentResult, buId,    │
                       │           │   userQuestion           │
                       │           ▼                          │
                       │  Foundry agent:                      │
                       │   wfm-sql-builder                    │
                       │   • MCP tool: getSchema              │
                       │   • text.format = json_schema        │
                       │     (SqlPlan, strict=True)           │
                       │                                      │
                       │  ┌────────────────────┐              │
                       │  │ QueryExecutorStep  │              │
                       │  └─────────┬──────────┘              │
                       │            │ structured_inputs:      │
                       │            │   sqlPlan, userLanguage │
                       │            ▼                         │
                       │  Foundry agent:                      │
                       │   wfm-query-executor                 │
                       │   • MCP tool: executeQuery           │
                       │   • plain natural language output    │
                       │                                      │
                       │     ctx.yield_output(AgentResponse)  │
                       └──────────────────────────────────────┘
                                       │
                                       ▼
                              Final natural-language answer
```

### Key design choices

- **SequentialBuilder receives `Executor` instances, not `FoundryAgent` instances.** This is what
  enables strongly-typed, pydantic-validated hand-off between steps. `FoundryAgent`s are used
  *inside* each executor.
- **Structured Outputs are enforced by the model** via `text.format = TextResponseFormatJsonSchema(strict=True)`.
  The system prompt does **not** need to say "respond as JSON…". It is physically impossible for
  the model to emit anything else at the decoder level.
- **Structured Inputs are declared on the agent definition** and supplied at call time via
  `options={"extra_body": {"structured_inputs": {...}}}`. The agent template references them
  with `{{name}}` placeholders inside `instructions`.
- **MCP tools** are filtered per-agent: each step can only call the minimal set of MCP tools
  required (`listTables`, `getSchema`, `executeQuery`). All tools use `require_approval.never` to
  avoid human-in-the-loop prompts during automated runs.

---

## 3. The three Foundry agents (current deployed state)

All three are **Prompt Agents** (`PromptAgentDefinition`) on model `gpt-5.2`, attached to the same
MCP server `wfm-data` (URL configured in `update_agents.py`).

| Agent name (env var)                              | Version (as of 2026-05-24) | `text.format`                     | `structured_inputs`                              | MCP tools allowed |
|---------------------------------------------------|----------------------------|------------------------------------|---------------------------------------------------|-------------------|
| `wfm-intent-classifier`  (`INTENT_AGENT_NAME`)    | v8                         | `IntentResult` (strict json_schema)| —                                                 | `listTables`      |
| `wfm-sql-builder`        (`SQL_BUILDER_AGENT_NAME`)| v7                         | `SqlPlan` (strict json_schema)     | `intentResult`, `buId`, `userQuestion`            | `getSchema`       |
| `wfm-query-executor`     (`QUERY_EXECUTOR_AGENT_NAME`)| v5                      | none (natural language)            | `sqlPlan`, `userLanguage`                         | `executeQuery`    |

You can re-verify this at any time via the Azure AI Projects SDK:

```python
client.agents.get_version(agent_name="wfm-sql-builder", agent_version="7").definition.text.format
# -> TextResponseFormatJsonSchema(name="SqlPlan", strict=True, schema={...})
```

> Foundry's portal UI does **not** currently expose `text.format` in the agent details view.
> Always inspect it via the SDK / REST.

---

## 4. Structured Outputs — exactly how it works

### 4.1 Why server-side, not prompt-side

OpenAI / Azure OpenAI Structured Outputs constrain **token decoding** to a context-free grammar
generated from the JSON Schema. When `strict=True`:

- Every required key must appear.
- No additional properties are allowed (`additionalProperties: false`).
- Enums become hard constraints.
- The output is guaranteed to `json.loads` and validate against the schema, with **no retries**
  and **no need for prompt-engineering tricks** like "respond only in JSON with no markdown".

This means we can keep the system prompt focused on *semantics* (what the agent must decide /
build), and let the schema handle *syntax*.

### 4.2 How we wire it on a Foundry Prompt Agent

Inside `update_agents.py`:

```python
from azure.ai.projects.models import (
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
)

INTENT_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {"type": "string", "enum": ["DataQuery", "Conversational", "OutOfScope"]},
        "candidate_tables": {"type": "array", "items": {"type": "string"}},
        "language_hint": {"type": "string"},
        "cache_action": {"type": "string", "enum": ["reuse", "refresh"]},
    },
    "required": ["intent", "candidate_tables", "language_hint", "cache_action"],
}

PromptAgentDefinition(
    model="gpt-5.2",
    instructions=INTENT_INSTRUCTIONS,
    tools=[...],
    text=PromptAgentDefinitionTextOptions(
        format=TextResponseFormatJsonSchema(
            name="IntentResult",
            strict=True,
            schema=INTENT_RESULT_SCHEMA,
        ),
    ),
)
```

This `PromptAgentDefinition` is then sent to Foundry via:

```python
client.agents.create_version(agent_name="wfm-intent-classifier", definition=...)
```

which **publishes a new immutable version** of the agent. Older versions remain available.

### 4.3 Strict-mode constraints we observed

OpenAI's strict json_schema mode requires:

- `additionalProperties: false` on every `object`.
- Every property declared in `properties` must appear in `required` (no truly optional fields).
- Nullable fields must use `"type": ["string", "null"]` (or similar), **not** `"nullable": true`.

That is why `SqlPlan.error` is typed `["string", "null"]` and *required*: it is always present,
just possibly null.

### 4.4 Consuming the structured output on the MAF side

```python
from pydantic import BaseModel

class IntentResult(BaseModel):
    intent: str
    candidate_tables: list[str] = Field(default_factory=list)
    language_hint: str = "en"
    cache_action: str = "reuse"

response = await intent_agent.run(user_question)
intent = IntentResult.model_validate_json(response.text)   # guaranteed to parse
```

No `try/except json.JSONDecodeError` is needed in practice because the model cannot return invalid
JSON. The Pydantic model is the *MAF-side mirror* of the schema declared on the agent.

---

## 5. Structured Inputs — exactly how it works

### 5.1 Declaring them on the agent

`PromptAgentDefinition.structured_inputs` is a `dict[str, StructuredInputDefinition]`.
The `StructuredInputDefinition` model has the following fields (verified via introspection of
`azure.ai.projects.models`):

```python
StructuredInputDefinition(
    description: Optional[str],
    default_value: Optional[Any],
    schema: Optional[dict[str, Any]],   # JSON Schema fragment
    required: Optional[bool],
)
```

⚠️ There is **no `type` keyword argument**. The shape of the value is described by `schema`.

Helpers in `update_agents.py`:

```python
def _str_input(description: str = "") -> StructuredInputDefinition:
    return StructuredInputDefinition(
        description=description,
        required=True,
        schema={"type": "string"},
    )

def _json_input(description: str = "") -> StructuredInputDefinition:
    return StructuredInputDefinition(
        description=description,
        required=True,
        schema={"type": "object", "additionalProperties": True},
    )
```

Example use on the SQL builder:

```python
structured_inputs={
    "intentResult": _json_input("Output object from the intent classifier."),
    "buId":         _str_input("Business Unit identifier ..."),
    "userQuestion": _str_input("Original natural-language question ..."),
}
```

The agent **`instructions`** then reference these inputs as Liquid-style placeholders:

```
Structured inputs:
- intentResult: {{intentResult}}
- buId: {{buId}}
- userQuestion: {{userQuestion}}
```

At runtime, Foundry interpolates the JSON of each structured input into the prompt before sending
it to the model.

### 5.2 Passing structured inputs at call time

`FoundryAgent.run(...)` accepts an `options` dict that is forwarded as `extra_body`:

```python
response = await sql_builder_agent.run(
    bundle.user_question,                              # the plain "user" turn
    options={
        "extra_body": {
            "structured_inputs": {
                "intentResult": bundle.intent_result.model_dump(),   # full JSON object
                "buId":         str(bundle.bu_id),                   # MUST be str
                "userQuestion": bundle.user_question,
            }
        }
    },
)
```

Important quirks discovered:

- **Field names must match exactly** what was declared in the definition
  (`intentResult`, not `intent_result`).
- **Types must match the declared schema.** Passing `bu_id` as `int` when the agent declared
  `"type": "string"` raises a server error like:
  `"buId integer but should be string"`. Always serialize with the right primitive type.
- The **first positional argument** of `agent.run()` is still required even when the agent reads
  *only* structured inputs. We pass either the actual user question (for `sql-builder`) or a
  short hint like `"Execute the SQL plan and answer the user."` (for `query-executor`) to keep
  traces readable.
- Missing required inputs surface as
  `"Required inputs [intentResult, buId, userQuestion] not provided"`.

---

## 6. Custom MAF Executors — bridging structured I/O

`SequentialBuilder` from `agent_framework.orchestrations` accepts heterogeneous participants:
agents OR `Executor` subclasses. We use `Executor` subclasses to gain full control over the
*shape* of messages flowing between steps.

### 6.1 Shared pydantic models (in `main_local.py`)

```python
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
```

`IntentBundle` / `SqlBundle` are *envelopes* that carry forward context (`user_question`,
`bu_id`, `language_hint`) that the next agent needs but cannot re-derive on its own.

### 6.2 The three executors

```python
class IntentStep(Executor):
    """Receives the initial conversation injected by SequentialBuilder."""

    @handler
    async def run(
        self,
        conversation: list[Message],          # NOTE: list[Message], not UserTurn
        ctx: WorkflowContext[IntentBundle],
    ) -> None:
        user_question = next(
            (m.text for m in reversed(conversation) if m.role == "user" and m.text),
            "",
        )
        response = await self._agent.run(user_question)
        intent = IntentResult.model_validate_json(response.text)
        await ctx.send_message(
            IntentBundle(
                user_question=user_question,
                bu_id=self._bu_id,
                intent_result=intent,
            )
        )


class SqlBuilderStep(Executor):
    @handler
    async def run(self, bundle: IntentBundle, ctx: WorkflowContext[SqlBundle]) -> None:
        structured_inputs = {
            "intentResult": bundle.intent_result.model_dump(),
            "buId":         str(bundle.bu_id),
            "userQuestion": bundle.user_question,
        }
        response = await self._agent.run(
            bundle.user_question,
            options={"extra_body": {"structured_inputs": structured_inputs}},
        )
        plan = SqlPlan.model_validate_json(response.text)
        await ctx.send_message(
            SqlBundle(sql_plan=plan, user_language=bundle.intent_result.language_hint)
        )


class QueryExecutorStep(Executor):
    @handler
    async def run(self, bundle: SqlBundle, ctx: WorkflowContext[Never, AgentResponse]) -> None:
        structured_inputs = {
            "sqlPlan":      bundle.sql_plan.model_dump(),
            "userLanguage": bundle.user_language,
        }
        response = await self._agent.run(
            "Execute the SQL plan and answer the user.",
            options={"extra_body": {"structured_inputs": structured_inputs}},
        )
        await ctx.yield_output(AgentResponse(messages=response.messages))
```

### 6.3 Building and running the workflow

```python
workflow = SequentialBuilder(
    participants=[
        IntentStep(intent_agent, bu_id=bu_id),
        SqlBuilderStep(sql_builder_agent),
        QueryExecutorStep(query_executor_agent),
    ]
).build()

events  = await workflow.run(user_question)
outputs = events.get_outputs()
final: AgentResponse = outputs[0]
```

### 6.4 Why each `WorkflowContext` type matters

- `IntentStep`: `WorkflowContext[IntentBundle]` — declares what this step emits.
- `SqlBuilderStep`: `WorkflowContext[SqlBundle]` — declares the next message type.
- `QueryExecutorStep`: `WorkflowContext[Never, AgentResponse]` — `Never` for outgoing messages
  (this is the terminator) and `AgentResponse` for the workflow's *final output* surfaced via
  `ctx.yield_output(...)`.

The MAF runtime uses these annotations to validate handler signatures and infer participant
chaining.

---

## 7. The agent-update script (`update_agents.py`)

A **single, idempotent** entry point for publishing new versions of all three agents.

### 7.1 Usage

```bash
python update_agents.py            # publishes a new version of all 3 agents
python update_agents.py intent     # publishes only wfm-intent-classifier
python update_agents.py sql        # publishes only wfm-sql-builder
python update_agents.py executor   # publishes only wfm-query-executor
python update_agents.py sql executor   # subset, in order
```

Output:

```
Updating wfm-intent-classifier ...
  -> wfm-intent-classifier:8 (version 8)
Updating wfm-sql-builder ...
  -> wfm-sql-builder:7 (version 7)
Updating wfm-query-executor ...
  -> wfm-query-executor:5 (version 5)
```

Each call to `client.agents.create_version(...)` mints an **immutable** new version. The
*default* version pointer (what `FoundryAgent(agent_name=...)` resolves to) typically follows
the newest published version, but you should verify your project's policy.

### 7.2 Code anatomy

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentDefinition,
    MCPTool, MCPToolFilter, MCPToolRequireApproval,
    PromptAgentDefinition, PromptAgentDefinitionTextOptions,
    StructuredInputDefinition,
    TextResponseFormatJsonSchema,
)

# 1) JSON Schemas (the "wire format")
INTENT_RESULT_SCHEMA = { ... }
SQL_PLAN_SCHEMA      = { ... }

# 2) System prompts (semantics only — no JSON formatting rules)
INTENT_INSTRUCTIONS     = """..."""
SQL_BUILDER_INSTRUCTIONS = """..."""
QUERY_EXECUTOR_INSTRUCTIONS = """..."""

# 3) Helpers
def _mcp_tool(allowed: list[str]) -> MCPTool: ...
def _str_input(description: str) -> StructuredInputDefinition: ...
def _json_input(description: str) -> StructuredInputDefinition: ...

# 4) Per-agent factories returning PromptAgentDefinition
def intent_definition()        -> PromptAgentDefinition: ...
def sql_builder_definition()   -> PromptAgentDefinition: ...
def query_executor_definition()-> PromptAgentDefinition: ...

# 5) Registry consumed by the CLI
AGENTS: dict[str, tuple[str, Callable[[], AgentDefinition]]] = {
    "intent":   ("wfm-intent-classifier", intent_definition),
    "sql":      ("wfm-sql-builder",       sql_builder_definition),
    "executor": ("wfm-query-executor",    query_executor_definition),
}
```

### 7.3 MCP tool wiring

```python
def _mcp_tool(allowed: list[str]) -> MCPTool:
    return MCPTool(
        server_label="wfm-data",
        server_url=MCP_SERVER_URL,
        allowed_tools=MCPToolFilter(tool_names=allowed),
        require_approval=MCPToolRequireApproval(
            never=MCPToolFilter(tool_names=allowed),
        ),
    )
```

- `allowed_tools` limits the agent to a whitelist of MCP tool names — defense-in-depth so the
  intent classifier cannot accidentally call `executeQuery`, etc.
- `require_approval=MCPToolRequireApproval(never=...)` disables the human-approval gate so the
  workflow runs unattended. ⚠️ For sensitive operations (mutations, exports), you would instead
  use `MCPToolRequireApproval(always=...)`.

### 7.4 Re-running safely

The script is safe to re-run: each invocation always creates a **new version** with the current
definition; it never mutates existing versions. Roll-back = point the agent at a previous
version in Foundry (or re-run with the old code on a branch).

---

## 8. Pitfalls & lessons learned (verified during this validation)

| Symptom | Root cause | Fix |
|---|---|---|
| `ImportError: AgentRunResponse` | Wrong type name | Use `AgentResponse` from `agent_framework` |
| `DefaultAzureCredential` async context manager fails | Sync version used | Import from `azure.identity.aio` |
| `structured_inputs` ignored when passed top-level | Wrong location | Pass via `options={"extra_body": {"structured_inputs": ...}}` |
| `response_format` rejected: "Not allowed when agent is specified" | Cannot override at call time once bound to a Foundry agent | Configure on the agent itself via `text.format=TextResponseFormatJsonSchema(...)` |
| `TypeCompatibilityError: list[Message] vs UserTurn` | First participant of `SequentialBuilder` receives `list[Message]` | Type the first handler as `list[Message]` and extract the user text manually |
| `MCPToolRequireApproval("never")` AttributeError | It's a model, not a string enum | `MCPToolRequireApproval(never=MCPToolFilter(tool_names=[...]))` |
| Server complains `buId integer but should be string` | Type drift between Python `int` and declared schema `string` | Cast: `str(bundle.bu_id)` |
| Strict json_schema rejected by Foundry | Missing `additionalProperties: false` or `required` for every key | Include them everywhere; use `["type","null"]` for nullable |
| `StructuredInputDefinition(type="string", ...)` raises | There is no `type` field | Use `schema={"type": "string"}` |
| `client.agents.get_version(agent_version="latest")` -> 400 | Foundry expects a numeric version string | Pass the actual version number as a string (`"7"`) |

---

## 9. Environment & configuration

`.env` (not committed) must contain:

```
FOUNDRY_PROJECT_ENDPOINT=https://<your-foundry-project>.services.ai.azure.com/api/projects/<projectId>
INTENT_AGENT_NAME=wfm-intent-classifier
SQL_BUILDER_AGENT_NAME=wfm-sql-builder
QUERY_EXECUTOR_AGENT_NAME=wfm-query-executor
BU_ID=1

# Telemetry (any OTLP collector — Azure Monitor exporter, OTel Collector, etc.)
OTEL_EXPORTER_OTLP_ENDPOINT=https://<your-otlp-endpoint>
# Optional:
# APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=...
```

`requirements.txt`:

```
agent-framework
azure-monitor-opentelemetry
```

Additional runtime dependencies (installed transitively or already present in the dev container):

- `azure-ai-projects` — Foundry control plane SDK (used in `update_agents.py`).
- `azure-identity` (sync) and `azure.identity.aio` (async) for auth.
- `python-dotenv`, `pydantic>=2`, `typing_extensions`.

Authentication: `DefaultAzureCredential` (works with `az login`, managed identity, env vars,
etc.). The dev-tunnel MCP server is reached from the agent itself (server-to-server) — no
client-side networking is required.

---

## 10. Telemetry

`main_local.py` initializes OpenTelemetry **after** loading `.env`, so the SDK can read
`OTEL_EXPORTER_OTLP_ENDPOINT` (and optionally `APPLICATIONINSIGHTS_CONNECTION_STRING`):

```python
from dotenv import load_dotenv
from agent_framework.observability import (
    configure_otel_providers,
    enable_sensitive_telemetry,
)

load_dotenv()
configure_otel_providers()
enable_sensitive_telemetry()   # includes prompts/completions in spans — turn off in prod if needed
```

Spans emitted include:

- The workflow run as a root span.
- One child span per executor.
- One child span per Foundry agent call, including the bound MCP tool invocations.

When `enable_sensitive_telemetry()` is active, span attributes contain prompts and completions.
For production, gate this behind an env flag.

---

## 11. Running everything end-to-end

```bash
# 1) (Re)publish the three agents if you changed prompts/schemas
python update_agents.py

# 2) Run the workflow with the hardcoded sample question
python main_local.py
```

Expected output (example):

```
===== Final Response =====
[wfm-query-executor]
En la unidad de negocio con bu_id = 1, hay 50 agentes distintos (conteo de agent_id únicos).
```

---

## 12. Verifying server-side configuration programmatically

Useful for CI and for proving the structured-output contract is in place even though the Foundry
portal does not show it:

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

client = AIProjectClient(
    endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

for name, ver in [
    ("wfm-intent-classifier", "8"),
    ("wfm-sql-builder",       "7"),
    ("wfm-query-executor",    "5"),
]:
    v   = client.agents.get_version(agent_name=name, agent_version=ver)
    fmt = getattr(getattr(v.definition, "text", None), "format", None)
    si  = getattr(v.definition, "structured_inputs", None)
    print(name, ver, type(fmt).__name__ if fmt else "—", list(si.keys()) if si else "—")
```

Sample output:

```
wfm-intent-classifier 8 TextResponseFormatJsonSchema —
wfm-sql-builder       7 TextResponseFormatJsonSchema ['intentResult', 'buId', 'userQuestion']
wfm-query-executor    5 —                            ['sqlPlan', 'userLanguage']
```

---

## 13. Reference: full JSON Schemas

### `IntentResult`

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "intent":           { "type": "string", "enum": ["DataQuery", "Conversational", "OutOfScope"] },
    "candidate_tables": { "type": "array",  "items": { "type": "string" } },
    "language_hint":    { "type": "string" },
    "cache_action":     { "type": "string", "enum": ["reuse", "refresh"] }
  },
  "required": ["intent", "candidate_tables", "language_hint", "cache_action"]
}
```

### `SqlPlan`

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "sql":         { "type": "string" },
    "tables_used": { "type": "array",  "items": { "type": "string" } },
    "assumptions": { "type": "array",  "items": { "type": "string" } },
    "explanation": { "type": "string" },
    "error":       { "type": ["string", "null"] }
  },
  "required": ["sql", "tables_used", "assumptions", "explanation", "error"]
}
```

---

## 14. Extension points (for the next iteration)

These are NOT implemented here, but the architecture supports them with minimal change:

1. **Validator / guard executor** between `QueryExecutorStep` and the terminator, with optional
   loop-back to `SqlBuilderStep` via a `WorkflowContext` edge.
2. **Deterministic SQL guardrail step** (using `sqlglot`) before invoking `executeQuery`:
   rejects non-SELECT statements, enforces `WHERE bu_id = ?`, caps `LIMIT`.
3. **Caching layer** keyed on `(bu_id, hash(intent_result))` honoring `cache_action`.
4. **Parallel fan-out** post-execution (`ConcurrentBuilder`) for follow-ups, chart suggestions,
   explanations.

Each of those can be added as a new `Executor` with explicit pydantic message types — the same
pattern used by the three steps documented above.

---

## 15. TL;DR for an LLM consuming this README

- `main_local.py` is the canonical local runtime entry point. It builds a `SequentialBuilder`
  of three custom `Executor`s. Each executor wraps one `FoundryAgent` (referenced by name, not
  created). The Foundry Hosted Agent variant `foundry_hosted/main_hosted.py` shares the same
  workflow code; it only adds `ResponsesHostServer` so the agent is reachable via the
  Responses protocol after `azd deploy`.
- Each Foundry agent's definition (instructions, MCP tools, structured inputs, structured
  outputs) is the source of truth and is published from `update_agents.py` via
  `AIProjectClient.agents.create_version(...)`. **JSON formatting rules are NOT in the prompts;
  they are encoded in `text.format = TextResponseFormatJsonSchema(strict=True, schema=...)`.**
- Inter-agent state travels as Pydantic models (`IntentBundle`, `SqlBundle`). Structured inputs
  are passed at call time via `options={"extra_body": {"structured_inputs": {...}}}` and must
  match the declared keys, types, and required-ness.
- The Foundry portal does **not** display `text.format`; use the SDK (`client.agents.get_version`)
  to verify it.
- The pipeline is observability-ready: telemetry is configured via `configure_otel_providers()`
  + `enable_sensitive_telemetry()` after `load_dotenv()`.

---

## 16. Hosted Agent Deployment (`azd deploy`) — full guide

This section captures the exact prerequisites, identities, RBAC, and runtime
configuration needed to deploy `foundry_hosted/main_hosted.py` to a Foundry
project. Most of these requirements are **not obvious** from the docs and were
discovered the hard way; the [`scripts/`](./scripts/) directory automates the
fixes.

### 16.1 Two-script workflow — TL;DR

```bash
# 0) First time only — scaffold the azd project from the manifest
azd ai agent init -m ./foundry_hosted/agent.manifest.yaml
azd env new wfm-data-assistant            # pick any name; this becomes AZURE_ENV_NAME

# 1) Set the values the manifest expects
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME gpt-5.2
azd env set BU_ID 1
azd env set MCP_SERVER_URL "https://<your-devtunnel-or-public-mcp-url>/mcp/"
# AZURE_TENANT_ID is needed by the azd postdeploy hook on some versions:
azd env set AZURE_TENANT_ID "$(az account show --query tenantId -o tsv)"

# 2) Verify everything before paying for a deploy
./scripts/preflight-check.sh

# 3) Deploy (fixes missing RBAC, deploys, smoke-tests)
./scripts/deploy-hosted-agent.sh --fix-rbac
```

The deploy script is **idempotent** — safe to re-run after partial failures.

### 16.2 What `scripts/preflight-check.sh` validates

| # | Check | Why it matters |
|---|---|---|
| 1 | `az`, `azd`, `docker`, `curl`, `jq`/`python3`, `azure.ai.agents` azd extension | `azd deploy` requires Docker locally to build the image and the azd extension `azure.ai.agents` to wire up Foundry-specific resources. |
| 2 | Active `az login` + active subscription | Both `azd` and `az` reuse your token; without a fresh sign-in `azd deploy` falls back to device-code or fails. |
| 3 | `azd env` populated with all required vars (see § 16.4) | Missing `AZURE_AI_MODEL_DEPLOYMENT_NAME` or `MCP_SERVER_URL` causes container start to crash with `KeyError`. |
| 4 | Foundry **account** + **project** exist and **both have system-assigned MIs enabled** | The two MIs are the principals that pull the image and talk to the agent's own runtime storage. |
| 5 | ACR exists | `azd deploy` pushes the image here. |
| 6 | `AcrPull` granted to both Foundry MIs on the ACR | Without this the container `ImagePullBackOff`s after `azd deploy` reports success. |
| 7 | **`Foundry User` granted to both Foundry MIs at the *account* scope** | THIS IS THE FIX. Without it, the agent's `/responses` call internally fails with `Foundry storage GET …/storage/history/item_ids -> 401` and the client sees `HTTP 500 PermissionDenied`. See § 16.5. |
| 8 | MCP server URL reachable | The agent reaches MCP from inside the Foundry-managed network. If you use a devtunnel, the tunnel must be running; if MCP talks to Azure SQL, the SQL firewall must allow your dev host. |

### 16.3 The three managed identities involved

There are **three** distinct principals you need to be aware of:

| Identity | Where it lives | Used for | Auto-created? |
|---|---|---|---|
| **Foundry account MI** | System-assigned MI on the AI Services account | Pulls the container image from ACR | Yes (when the account is created with `identity.type=SystemAssigned`). |
| **Foundry project MI** | System-assigned MI on the project sub-resource | Image pull + project-level data plane | Yes (when the project is created with `identity.type=SystemAssigned`). |
| **Agent runtime MI** | Per-agent instance identity managed by Foundry | Token presented by the container when it calls back into `/storage/history`, `/conversations`, etc. | Yes — Foundry mints/rotates it; not directly visible in the Azure portal. Resolve it via REST: `GET {endpoint}/agents/{name}` → `instance_identity.principal_id`. |

The Foundry account MI and project MI are the ones you can see with:

```bash
az cognitiveservices account show -n <account> -g <rg> --query identity.principalId -o tsv
az resource show --ids <project_id> --api-version 2025-06-01 --query identity.principalId -o tsv
```

The agent runtime MI is fetched at the end of `deploy-hosted-agent.sh` via the
Foundry REST API.

### 16.4 Required `azd env` variables

The `azd env get-value <var>` interface is the canonical source for the variables consumed by the deploy. After `azd ai agent init`, the manifest populates many of these automatically; you only need to set the ones marked **(user-set)**.

| Variable | Set by | Used by |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP` | `azd ai agent init` (mostly) | `az`/`azd` plumbing. `AZURE_TENANT_ID` you may need to set manually (see § 16.7). |
| `AZURE_AI_PROJECT_ID`, `FOUNDRY_PROJECT_ENDPOINT` | `azd ai agent init` | All Foundry SDK calls. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | **(user-set)** | Container reads it as the chat model name. `${AZURE_AI_MODEL_DEPLOYMENT_NAME}` template in `agent.yaml` resolves to this. |
| `AZURE_CONTAINER_REGISTRY_ENDPOINT` | `azd provision` / pre-existing | Image push target. |
| `BU_ID` | **(user-set)** | Workflow scoping. |
| `MCP_SERVER_URL` | **(user-set)** | Agent's MCP tool endpoint. |
| `AGENT_WFM_NAME`, `AGENT_WFM_VERSION`, `AGENT_WFM_ENDPOINT`, `AGENT_WFM_RESPONSES_ENDPOINT` | Populated by `azd deploy` post-success | Reference values for invoking the agent. |

### 16.5 The storage-401 trap (§ #1 hidden requirement)

When the agent receives a `/responses` request, the agent-server SDK fetches
conversation history from Foundry's **managed** memory store
(`Microsoft.CognitiveServices`) at the URL
`{endpoint}/api/projects/{project}/storage/history/item_ids?api-version=v1`.

This call uses **the Foundry MI's token** (account MI + project MI flow). If
neither MI has `Foundry User` on the **account** scope, the call returns 401
and the inbound `/responses` request fails with HTTP 500
`PermissionDenied`.

The Foundry docs (Memory page — see references below) say:

> Assign **Foundry User** to the managed identity of your project.
>
> Troubleshooting → "Requests fail with an authentication or authorization
> error → Your identity *or the project managed identity* doesn't have the
> required roles."

Memory in Foundry Agent Service is **fully managed** — there is **no**
storage account you need to create. The 401 is solely a missing role
assignment.

The cure (run automatically by `scripts/deploy-hosted-agent.sh --fix-rbac`):

```bash
SCOPE="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"

for PID in <ACCOUNT_MI_PID> <PROJECT_MI_PID>; do
  az role assignment create \
    --assignee-object-id "$PID" \
    --assignee-principal-type ServicePrincipal \
    --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" \
    --scope "$SCOPE"
done
```

Role definition IDs (well-known, don't change):

| Role | ID |
|---|---|
| AcrPull | `7f951dda-4ed3-4680-a7ca-43fe172d538d` |
| Foundry User | `53ca6127-db72-4b80-b1b0-d745d6d5456d` |
| Foundry Owner | `c883944f-8b7b-4483-af10-35834be79c4a` |
| Foundry Account Owner | `e47c6f54-e4a2-4754-9501-8e0985b135e1` |
| Foundry Project Manager | `eadc314b-1a2d-4efa-be10-5d325db5065e` |

Note: the older role names (`Azure AI User`, `Azure AI Owner`, etc.) are
synonyms — same role IDs, different display names.

### 16.6 Reserved environment variables in `agent.yaml`

The Foundry hosting platform **reserves** any env var starting with
`FOUNDRY_*` or `AGENT_*` for its own purposes (Foundry injects them at
runtime). If you declare one in `agent.yaml` (e.g. `FOUNDRY_MODEL`), the
container fails to start with an error like:

```
Reserved environment variable name: FOUNDRY_MODEL
```

The fix is to use a non-reserved alias and read it from your code:

```yaml
# agent.yaml
environment_variables:
  - name: AZURE_AI_MODEL_DEPLOYMENT_NAME   # NOT reserved → declare freely
    value: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}
```

```python
# main_hosted.py
FOUNDRY_MODEL = (
    os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or os.environ["FOUNDRY_MODEL"]   # fallback for local dev
)
```

The platform auto-injects these without you declaring them — never declare
them in `agent.yaml`:

- `FOUNDRY_PROJECT_ENDPOINT`
- `FOUNDRY_AGENT_NAME`
- `FOUNDRY_AGENT_VERSION`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`
- `AGENT_SERVER_HOSTED` (the marker we read to detect hosted mode)

### 16.7 Postdeploy 404 — cosmetic, ignore it

After `Deploying service wfm: Done`, you may see:

```
ERROR: failed invoking event handlers for 'postdeploy', failed to fetch
agent version for wfm/6: GET …/agents/wfm/versions/6 → 404
```

The postdeploy hook in some `azd` versions looks up the agent by **azd
service name** (`wfm`) instead of by the actual **agent name**
(`wfm-data-assistant`). The deploy itself succeeded — verify with:

```bash
az ai agent show -e wfm-data-assistant  # azd shorthand
# or
azd ai agent show
```

The newer `azure.ai.agents` extension fixed this; if you're affected, set
`AZURE_TENANT_ID` in your azd env to keep the hook from also complaining
about missing tenant. The script treats this as non-fatal.

### 16.8 Enabling rich GenAI tracing

The Foundry hosting platform pre-configures `microsoft-opentelemetry` to ship
spans to App Insights via `APPLICATIONINSIGHTS_CONNECTION_STRING`. To get
**rich** spans that include `gen_ai.system`, prompt/completion text, and
tool-call arguments/results, set this env var in `agent.yaml`:

```yaml
environment_variables:
  - name: AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING
    value: "true"
```

Without it you'll see the warning in container logs:

```
azure.ai.projects.telemetry._ai_project_instrumentor:
GenAI tracing is not enabled. Set environment variable
AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true
```

This is already enabled in the committed `agent.yaml` and
`agent.manifest.yaml`.

### 16.9 What the deploy script actually does

Reading `scripts/deploy-hosted-agent.sh` top-to-bottom:

```text
0. Resolve identities (account MI, project MI, ACR ID, account scope) from
   the active azd env (no hard-coded values).

1. Run preflight-check.sh.
   - If it reports missing RBAC and --fix-rbac was passed, continue;
     otherwise exit 1 and print the role-assignment commands to run.

2. If --fix-rbac:
   - Idempotently create the four required role assignments
     (AcrPull × 2, Foundry User × 2).

3. Run `azd deploy --no-prompt`.
   - Tolerates the cosmetic postdeploy 404 (see § 16.7).

4. Resolve the agent runtime MI via Foundry REST
   (GET /agents/<name> → instance_identity.principal_id) and grant
   Foundry User to it as well (defensive — usually unnecessary).

5. Sleep 60 s for RBAC propagation when --fix-rbac applied changes.

6. Smoke-test via `azd ai agent invoke --new-conversation --new-session`.
   - Surfaces "PermissionDenied" as a clear failure indicating RBAC
     propagation isn't complete yet.
```

### 16.10 Troubleshooting cheatsheet

| Symptom | Likely cause | Resolution |
|---|---|---|
| `azd deploy` exits with `Reserved environment variable name: FOUNDRY_*` | You declared a reserved var in `agent.yaml` | Remove it; use an `AZURE_AI_*` alias and read both keys in code. |
| `ImagePullBackOff` after `azd deploy` reports success | Foundry MI(s) missing `AcrPull` on the ACR | `./scripts/deploy-hosted-agent.sh --fix-rbac` |
| `HTTP 500 PermissionDenied` returned to the client; container logs show `Foundry storage GET .../storage/history/item_ids -> 401` | Foundry MI(s) missing `Foundry User` on the **account** scope | Same — `--fix-rbac` |
| Container crashes on start: `KeyError: 'FOUNDRY_MODEL'` | Neither `FOUNDRY_MODEL` (local) nor `AZURE_AI_MODEL_DEPLOYMENT_NAME` (hosted) is set | `azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME <model-deployment>` |
| MCP tool calls fail with `Database '...' is not currently available` (SQL error 40613) | Azure SQL serverless paused **or** firewall blocking the dev host | Cold-start: retry after 30–60 s. Firewall: add your dev host IP to the Azure SQL firewall rules. |
| `azd deploy` fails with `Docker daemon not running` in dev container | Docker-in-docker not enabled | Add the dev container feature `ghcr.io/devcontainers/features/docker-in-docker:2` and rebuild the container. |
| Postdeploy: `AZURE_TENANT_ID not set` | Older `azd` postdeploy hook needs it explicitly | `azd env set AZURE_TENANT_ID "$(az account show --query tenantId -o tsv)"` |
| Container logs: `GenAI tracing is not enabled` warning | `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING` not declared | Already declared in `agent.yaml`; re-deploy. |

### 16.11 Inspecting the deployed agent

```bash
# Stream container logs (latest invocation session)
azd ai agent monitor --tail 200

# Inspect the deployed agent + version + endpoints
azd ai agent show

# Direct REST inspection (no SDK)
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$(azd env get-value FOUNDRY_PROJECT_ENDPOINT)/agents/$(azd env get-value AGENT_WFM_NAME)?api-version=v1" \
  | python3 -m json.tool

# Send a one-off message
azd ai agent invoke --new-conversation --new-session "¿Cuántos agentes hay en mi organización?"
```

### 16.12 References

- [Foundry Hosted Agents — Memory usage docs (the source of the Foundry User requirement)](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/memory-usage?pivots=python)
- [Foundry RBAC roles](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
- [`azd` AI agents extension](https://github.com/Azure/azd-ext-ai-agents)

---

## 17. Project status & known platform gaps (May 2026)

This section documents **where the project stands**, the platform limitation
discovered when trying to wire Cosmos DB to the Foundry Hosted Agent, the
exhaustive investigation that confirmed it, and the two viable alternatives.

### 17.1 What works today (verified end-to-end)

| Variant | File | Multi-turn | Persistence | Status |
|---|---|---|---|---|
| Local single-turn | `main_local.py` | ❌ | — | ✅ Works |
| Local multi-turn | `main_local_multiturn.py` | ✅ | Cosmos DB | ✅ Works (uses your `az login` identity) |
| Hosted — native checkpoints | `foundry_hosted/main_hosted_native.py` | ✅ | Foundry workflow checkpoints | ✅ Deployed as v9, 3-turn validated |
| Hosted — Cosmos | `foundry_hosted/main_hosted.py` | ✅ (code correct) | Cosmos DB | ❌ Blocked — see § 17.2 |

### 17.2 The Cosmos DB × Foundry Hosted Agent platform gap

#### Background

`main_local_multiturn.py` uses `CosmosHistoryProvider` from
`agent-framework-azure-cosmos` to persist conversation history.  When it runs
locally, `DefaultAzureCredential` resolves to your `az login` user token, which
has `Cosmos DB Built-in Data Contributor` assigned — so it works perfectly.

When the same code runs inside a Foundry Hosted Agent container,
`DefaultAzureCredential` resolves to the **agent's runtime identity** instead.
This is where the gap surfaces.

#### The agent runtime identity: `ServiceIdentity`

Every Foundry Hosted Agent version is assigned a dedicated principal by the
Foundry platform. You can inspect it via:

```bash
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "$(azd env get-value FOUNDRY_PROJECT_ENDPOINT)/agents/wfm-data-assistant?api-version=v1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['instance_identity'],indent=2))"
# → {"principal_id": "b5849610-…", "client_id": "b5849610-…"}
```

Querying Microsoft Graph for that principal reveals:

```json
{
  "@odata.type": "#microsoft.graph.agentIdentity",
  "id": "b5849610-eeb0-41d1-8173-be7aef376bfd",
  "displayName": "calabriomafpoc-foundry-…-wfm-data-assistant-AgentIdentity",
  "servicePrincipalType": "ServiceIdentity"
}
```

This is **not** a regular `ServicePrincipal`, `ManagedIdentity`, or `User`. It
is a new Entra principal subtype introduced for agentic workloads
(`microsoft.graph.agentIdentity` / `ServiceIdentity`).

#### Why Cosmos DB data-plane RBAC rejects it

Cosmos DB uses its own RBAC engine (`az cosmosdb sql role assignment`) that is
entirely separate from ARM RBAC. Internally, the Cosmos RBAC engine resolves
the principal type via Microsoft Graph. Its internal whitelist only accepts:

| Accepted | Rejected |
|---|---|
| `User` | `ServiceIdentity` ← agent runtime |
| `Group` | |
| `ServicePrincipal` | |
| `ManagedIdentity` | |

When you try to create a Cosmos SQL role assignment for a `ServiceIdentity`
principal, the CLI silently returns `{"status": "Enqueued"}` but the assignment
is never written. Polling for the assignment returns `404 NotFound`. Attempting
to invoke the agent then yields:

```
(Forbidden) Request blocked by Auth calabriomafpoc-cosmos:
Request is blocked because principal [b5849610-…] does not have required RBAC
permissions to perform action [Microsoft.DocumentDB/databaseAccounts/readMetadata].
ActivityId: …, Code: Forbidden
```

#### Every bypass that was attempted and why it failed

| Approach | Why it fails |
|---|---|
| `az cosmosdb sql role assignment create --principal-id <agent-pid>` | CLI returns `Enqueued`, assignment never lands. Principal type rejected server-side. |
| Raw ARM `PUT .../sqlRoleAssignments/<guid>?api-version=2024-08-15` | Returns `HTTP 202 {"status":"Enqueued"}`, then `GET` returns `404`. The Cosmos control-plane silently discards the write because it cannot map `ServiceIdentity` to a supported type. |
| Same with `api-version=2025-11-01-preview` | Identical behaviour — same silent rejection. |
| `az role assignment create --assignee-principal-type ServicePrincipal` (the ARM RBAC trick) | `--assignee-principal-type` is an **ARM RBAC** flag. `az cosmosdb sql role assignment create` has no equivalent; Cosmos data-plane RBAC has no such override. |
| Using the blueprint identity (`principal_id: 39754795-…`, `type: Application`) instead | The blueprint identity is the design-time app registration, not the token presented by the running container. The container token always uses the `instance_identity` (`ServiceIdentity`). |
| Using `AZURE_CLIENT_ID` env var to force a specific client ID at token acquisition | The credential chain inside the container resolves to the agent's own identity regardless; there is no hook to substitute a different UAMI at this level via `agent.yaml`. |

#### Root cause summary

Cosmos DB data-plane RBAC was designed before Entra introduced `ServiceIdentity`
(the `microsoft.graph.agentIdentity` subtype). Its internal principal resolver
treats any unrecognized type as `Unfamiliar` and refuses to create assignments
for it. This is a **Cosmos DB × Foundry identity compatibility gap at the
platform level** — not a configuration mistake. It requires a fix from the
Azure Cosmos DB and/or Microsoft Foundry teams.

#### Evidence trail

```
App Insights / container logs confirmed:
- HTTP 403, x-ms-substatus: 5301
- Principal: b5849610-eeb0-41d1-8173-be7aef376bfd
- Action: Microsoft.DocumentDB/databaseAccounts/readMetadata
- Resource: dbs/agent-framework/colls/chat-history

Graph API confirmed:
- @odata.type: #microsoft.graph.agentIdentity
- servicePrincipalType: ServiceIdentity
- createdByAppId: 0736f41a-0425-4b46-bdb5-1563eff02385 (Microsoft Foundry SP)

ARM RBAC list confirmed (for contrast):
- Project MI (5ed45ef1-…, type ManagedIdentity): AcrPull ✅, Foundry User ✅
- Account MI (f04da8ed-…, type ManagedIdentity): same ✅
- Agent instance_identity (b5849610-…, type ServiceIdentity): CANNOT receive Cosmos data-plane role
```

### 17.3 Alternative A evaluated: Azure Blob Storage

Azure Blob Storage uses **ARM RBAC** (not a separate engine), which accepts
`--assignee-principal-type ServicePrincipal` as an override. This means:

```bash
az role assignment create \
  --assignee-object-id "b5849610-…" \
  --assignee-principal-type ServicePrincipal \   # ← this works in ARM RBAC
  --role "ba92f5b4-2d11-453d-a403-e96b0029c9fe" \  # Storage Blob Data Contributor
  --scope "/subscriptions/…/storageAccounts/<account>"
```

This path is viable and has been confirmed to work via `ensure_assignment` in
`scripts/deploy-hosted-agent.sh`. It requires:

1. A new Storage Account (or a container within an existing one).
2. A `BlobHistoryProvider` class (~80 LOC) using `azure-storage-blob.aio`.
3. Replacing `CosmosHistoryProvider` in `main_hosted.py`.
4. Adding `AZURE_STORAGE_*` env vars to `agent.yaml`.

Not yet implemented; kept as a documented option for future iterations.

### 17.4 Alternative B chosen: Foundry native workflow checkpoints

The `ResponsesHostServer._handle_inner_workflow` code in the MAF SDK
(`agent_framework_foundry_hosting._responses`) implements multi-turn workflow
execution via **MAF workflow checkpoints** on a `FileCheckpointStorage` keyed
by the inbound `conversation_id` (or `previous_response_id`) and stored on the
Foundry session's persistent filesystem (`/sessions/$HOME`).

Key insight: the `WorkflowContext` exposed to every `Executor` provides
`get_state(key, default)` / `set_state(key, value)`. The MAF runner serialises
this `State` dict into every checkpoint. Foundry restores it on the next turn
before running the workflow again. This is exactly equivalent to what we were
doing with Cosmos — except the storage backend is built into the platform.

The implementation (`main_hosted_native.py`) makes three changes vs the Cosmos
variant:

| Before (Cosmos) | After (native checkpoints) |
|---|---|
| `prior = await self._provider.get_messages(sid)` | `prior_entries = ctx.get_state("history_messages", [])` |
| `await self._provider.save_messages(sid, [...])` | `ctx.set_state("history_messages", updated)` |
| `_SESSION_ID_CTX` + custom `response_handler` wrapper | Removed — Foundry keys checkpoints automatically |
| `AZURE_COSMOS_*` env vars in `agent.yaml` | Removed — no external storage |

#### Validated end-to-end (2026-05-26, Foundry Hosted Agent v9)

```
Turn 1  "¿Cuántos agentes activos hay en mi organización?"
→ "Hay 50 agentes activos en tu organización."

Turn 2  "¿y cuántos de esos pertenecen al equipo 1?"   ← anaphora: "de esos" = agentes activos
→ "Hay 0 agentes activos que pertenecen al equipo 1."
→ IntentStep resolved "de esos" via prior history from checkpoint ✅

Turn 3  "Resume brevemente lo que hemos hablado hasta ahora"  ← meta-question
→ "Me preguntaste cuántos agentes activos hay (50). Luego cuántos del equipo 1 (0)."
→ recall_conversation tool served snapshot from _HISTORY_SNAPSHOT_CTX ✅
```

No Cosmos traffic. No 403. Zero new Azure resources.

#### Known limitation: conversation grouping in Foundry UI

When the client chains turns via `previous_response_id` (the default for
`azd ai agent invoke`), each response lands under a different checkpoint
directory (keyed by its own `response_id`). The Foundry Traces UI displays each
response as a separate row because there is no server-side `conv_*` object
linking them. Functionally the multi-turn works correctly; the limitation is
purely observational.

To get proper grouping in the UI, the client must:
1. Create a conversation object server-side:
   `POST {endpoint}/conversations?api-version=2025-11-15-preview`
2. Pass it in every request body: `{"input":"…","conversation":"conv_abc123"}`

This forces `context.conversation_id` to be set, which causes Foundry to use a
single stable checkpoint directory for all turns of that conversation and groups
them in the UI.

### 17.5 Recommended path forward

| Goal | Recommended action |
|---|---|
| **Development / demos / internal use** | Run `main_local_multiturn.py` directly. Full Cosmos-backed multi-turn, App Insights traces, `recall_conversation` tool — everything works with your `az login` identity. |
| **Production Foundry Hosted Agent** | Keep `main_hosted_native.py` (v9). Multi-turn works. If UI conversation grouping matters, add `conv_*` creation to the client. |
| **Hosted Agent + Cosmos history** | Implement `BlobHistoryProvider` (Alternative A, §17.3) or wait for Microsoft to add `ServiceIdentity` support to Cosmos data-plane RBAC. |
| **Cross-conversation memory / bu_id indexing** | Either Alternative A (Blob with custom index) or waiting for Cosmos fix. Native checkpoints are per-conversation silos. |

