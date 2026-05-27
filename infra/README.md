# Infra — Bicep + APIM policies + azd

> All Azure infrastructure declared as code.

📖 See [PLAN.md §10](../PLAN.md#10-azure-infrastructure).

## Status

**Phase 0** — skeleton. Implementation in **Phase 4 (APIM)** + **Phase 5 (Bicep + azd)**.

## Planned structure

```
main.bicep
main.parameters.json
modules/
├── containerapps.bicep
├── apim.bicep
├── cosmos.bicep
├── keyvault.bicep
├── acr.bicep
├── loganalytics.bicep
├── appinsights.bicep
├── sql.bicep
└── network.bicep
apim-policies/
├── fragments/
│   ├── auth-validation.xml
│   ├── bu-resolution.xml
│   ├── hmac-sign.xml
│   └── rate-limit-per-user.xml
└── apis/
    ├── chat-api.xml
    └── mcp-api.xml
```

## Deploy (once it exists)

```bash
azd up
```

## Environment variables (azd)

See [PLAN.md §14 Infra](../PLAN.md#14-environment-variables-inventory).
