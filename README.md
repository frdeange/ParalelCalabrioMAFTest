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
├── main.py                # Legacy v1 — naive SequentialBuilder([FoundryAgent, ...])
├── main_v2.py             # ✅ Production-style pipeline with custom Executors (PRIMARY)
├── update_agents.py       # ✅ Single script that publishes new versions of the 3 Foundry agents
├── requirements.txt
├── .env                   # Endpoints, agent names, OTEL config (NOT committed)
└── promptAgents/          # Reference YAMLs for the 3 agents (documentation only)
    ├── intent-classifier.yaml
    ├── sql-builder.yaml
    └── query-executor.yaml
```

`promptAgents/*.yaml` are **only historical/reference docs** of the prompt content. The actual
source of truth for the deployed agents is `update_agents.py` (instructions, JSON Schemas and
structured-input declarations are embedded there as Python constants).

---

## 2. High-level architecture

```
                       ┌──────────────────────────────────────┐
 user_question ───►    │            main_v2.py                │
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

### 6.1 Shared pydantic models (in `main_v2.py`)

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

`main_v2.py` initializes OpenTelemetry **after** loading `.env`, so the SDK can read
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
python main_v2.py
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

- `main_v2.py` is the canonical runtime entry point. It builds a `SequentialBuilder` of three
  custom `Executor`s. Each executor wraps one `FoundryAgent` (referenced by name, not created).
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
