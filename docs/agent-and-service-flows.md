# Agent and Service Flows - Detailed Runtime Sequences

## 1. Purpose

This document provides sequence-level detail for how requests move through the system at runtime. It is intended for:

- Debugging and incident response.
- Presentation material that needs concrete flow diagrams.
- Implementation checks to avoid architecture drift.

The flows reflect current implementation and near-term design intent from `PLAN.md` and ADRs.

## 2. Main Runtime Actors

- Browser user session.
- Frontend (Next.js + MSAL + AG-UI client).
- APIM (JWT validation, BU resolution, HMAC signing).
- Backend (FastAPI + AG-UI endpoint + MAF workflow).
- MCP (FastMCP `schema.*` and `query.*` tools).
- Azure SQL.
- Cosmos DB.
- Foundry model endpoint.

## 3. Authentication and Session Bootstrap Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant FE as Frontend
    participant Entra as Entra ID

    User->>FE: Open app
    FE->>FE: Evaluate auth state
    alt Not authenticated
        FE->>Entra: loginRedirect(openid/profile/email + API scope)
        Entra-->>FE: Auth response
    end
    FE->>FE: Store account/session and enable /chat route
```

Important notes:

- Frontend uses MSAL redirect flow.
- Access token retrieval is attempted silently first for API requests.
- On silent acquisition failure, frontend triggers redirect-based token acquisition.

## 4. Chat Request Lifecycle (Data Query)

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant FE as Frontend Chat
    participant APIM
    participant BE as Backend /agui
    participant INT as IntentStep
    participant SQLB as SqlBuilderStep
    participant QEX as QueryExecutorStep
    participant MCP
    participant SQL as Azure SQL
    participant COSMOS as Cosmos DB

    User->>FE: Submit prompt
    FE->>FE: Build full message history + stable thread_id
    FE->>APIM: POST /agui with Bearer token

    APIM->>APIM: Validate JWT
    APIM->>APIM: Resolve BU (4-layer chain)
    APIM->>APIM: Build signed identity headers
    APIM->>BE: Forward request

    BE->>BE: Verify HMAC and parse caller context
    BE->>COSMOS: Load conversation context for thread

    BE->>INT: Run intent classification
    INT-->>BE: IntentBundle

    BE->>SQLB: Run SQL builder
    SQLB->>MCP: schema.list_tables
    MCP->>SQL: Read catalog tables
    SQL-->>MCP: tables metadata
    MCP-->>SQLB: tables

    SQLB->>MCP: schema.describe_table (selected tables)
    MCP->>SQL: Read catalog columns/joins
    SQL-->>MCP: schema details
    MCP-->>SQLB: details

    SQLB-->>BE: SqlBundle
    BE->>QEX: Execute query step
    QEX->>MCP: query.execute(sql)
    MCP->>MCP: Validate SQL AST + allowlist
    MCP->>SQL: Execute read-only query
    SQL-->>MCP: rows (+ truncation detection)
    MCP-->>QEX: rows + flags

    QEX-->>BE: AgentResponse
    BE->>COSMOS: Persist turn history
    BE-->>FE: SSE tokens and lifecycle events
    FE-->>User: Render streaming answer
```

## 5. Chat Request Lifecycle (Conversational / Out-of-Scope)

```mermaid
sequenceDiagram
    autonumber
    participant FE as Frontend
    participant APIM
    participant BE as Backend
    participant WF as Workflow

    FE->>APIM: POST /agui
    APIM->>BE: Signed request forward
    BE->>WF: Execute workflow
    WF-->>WF: Intent indicates conversational/out-of-scope path
    WF-->>BE: Final natural-language response
    BE-->>FE: SSE response stream
```

In this path, SQL execution may be skipped or replaced by a safe fallback response depending on plan validity.

## 6. APIM BU Resolution Decision Flow

```mermaid
flowchart TD
    A[Request arrives at APIM] --> B{Valid JWT?}
    B -- No --> Z[Reject request]
    B -- Yes --> C{JWT has extension_bu_id?}
    C -- Yes --> C1[Use claim BU]
    C -- No --> D{Email domain in map?}
    D -- Yes --> D1[Use mapped BU]
    D -- No --> E{Debug BU override allowed and present?}
    E -- Yes --> E1[Use x-debug-bu]
    E -- No --> F[Use BU_ID_DEFAULT]

    C1 --> G[Set x-bu-id header]
    D1 --> G
    E1 --> G
    F --> G
    G --> H[Sign identity headers via HMAC]
    H --> I[Forward to backend/MCP]
```

## 7. Backend Identity Verification Flow

```mermaid
flowchart TD
    A[Backend receives request] --> B{Has x-bu-id?}
    B -- No --> B1[400 Bad Request]
    B -- Yes --> C{Has all identity headers and signature?}
    C -- No --> C1[401 Unauthorized]
    C -- Yes --> D[Build canonical payload]
    D --> E{HMAC signature valid?}
    E -- No --> E1[401 Unauthorized]
    E -- Yes --> F[Construct Caller object]
    F --> G[Proceed to workflow]
```

## 8. SQL Validation and Execution Flow in MCP

```mermaid
flowchart TD
    A[query.execute receives SQL] --> B[Resolve row cap]
    B --> C[Load allowlist from _metadata.catalog_tables]
    C --> D[Validate SQL with sqlglot AST gate]
    D --> E{Validation ok?}
    E -- No --> E1[Emit rejected telemetry + return tool error]
    E -- Yes --> F[Execute normalized SQL via Entra ODBC client]
    F --> G[Fetch max_rows + 1]
    G --> H[Build rows + truncated flag]
    H --> I[Emit success telemetry]
    I --> J[Return rows to caller]
```

## 9. Frontend SSE Event Handling Flow

```mermaid
flowchart LR
    A[SSE frame arrives] --> B{JSON data frame?}
    B -- No --> Z[Ignore]
    B -- Yes --> C{Event type}
    C -- STEP_STARTED --> D[Update friendly progress label]
    C -- TEXT_MESSAGE_CONTENT --> E[Append token to assistant message]
    C -- RUN_ERROR --> F[Display error state]
    C -- RUN_FINISHED --> G[Stop loading]
    C -- Other --> Z
```

## 10. AG-UI Event Mapping Used by UI

| Event type | UI behavior |
|---|---|
| `STEP_STARTED` | updates progress state shown in bubble |
| `TEXT_MESSAGE_CONTENT` | appends streaming token text |
| `RUN_ERROR` | shows recoverable user-visible error |
| `RUN_FINISHED` | marks completion and unlocks input |

## 11. Failure Scenarios and Observable Symptoms

### 11.1 Token acquisition failure

- Symptom: frontend cannot call backend.
- Surface: user-visible auth error or redirect.
- Likely root causes: expired session, missing scope, Entra app misconfiguration.

### 11.2 HMAC mismatch

- Symptom: backend returns 401.
- Surface: AG-UI call fails before workflow execution.
- Likely root causes: APIM signing drift, shared secret mismatch, missing headers.

### 11.3 SQL validation rejection

- Symptom: query tool error, assistant may return fallback guidance.
- Surface: MCP rejection telemetry (`outcome=rejected`).
- Likely root causes: forbidden SQL node, unknown table, malformed statement.

### 11.4 Truncated query results

- Symptom: answer based on capped result set.
- Surface: MCP returns `truncated=true`.
- Likely root causes: broad query with insufficient filtering.

## 12. Throughput and Scalability Considerations

- Frontend scales by concurrent users and SSE connection load.
- Backend scales by AG-UI request concurrency, model latency, and MCP round trips.
- MCP scales by query volume and SQL latency.
- SQL remains the primary downstream bottleneck for heavy analytical prompts.

## 13. Traceability Matrix (Flow -> Source)

| Flow concern | Primary source |
|---|---|
| Workflow composition | `apps/backend/app/workflow/build.py` |
| Intent/SQL/Query step contracts | `apps/backend/app/workflow/schemas.py` |
| Identity verification | `apps/backend/app/deps/identity.py` |
| MCP namespace mounting | `apps/mcp/app/main.py` |
| SQL validation | `apps/mcp/app/security/sql_validator.py` |
| Frontend AG-UI SSE handling | `apps/frontend/lib/agui/client.ts` |

## 14. Suggested Presentation Usage

Use this document to generate:

1. Sequence animation slides.
2. Security and trust-boundary slides.
3. Error-handling and resilience slides.
4. Runbook-ready troubleshooting trees.
