# Infra — Bicep + APIM policies + azd

> Toda la infraestructura Azure declarada como código.

📖 Ver [PLAN.md §10](../PLAN.md#10-infraestructura-azure).

## Estado

**Phase 0** — esqueleto. Implementación en **Phase 4 (APIM)** + **Phase 5 (Bicep + azd)**.

## Estructura prevista

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

## Deploy (cuando exista)

```bash
azd up
```

## Variables de entorno (azd)

Ver [PLAN.md §14 Infra](../PLAN.md#14-inventario-de-variables-de-entorno).
