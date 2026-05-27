---
name: "⚙️ Infrastructure"
about: "Cambios en Bicep, azd, APIM, Container Apps, networking"
title: "[infra] "
labels: ["⚙️ infra", "🏛️ infra"]
assignees: []
---

## ⚙️ Descripción

<!-- ¿Qué recurso/configuración cambia? -->

## 🎯 Motivación

<!-- Problema que resuelve. -->

## 📋 Criterios de aceptación

- [ ] Bicep compila sin warnings
- [ ] `az deployment ... validate` OK
- [ ] `what-if` revisado
- [ ] Coste estimado documentado si es nuevo recurso
- [ ] RBAC mínimos aplicados (no Owner / Contributor a granel)
- [ ] Secrets en KV, nunca en Bicep parameters
- [ ] Workflow `infra-validate` verde

## 🗂️ Archivos a tocar

<!-- ej. infra/main.bicep, infra/modules/apim.bicep -->

## 🔐 Consideraciones de seguridad

<!-- Public endpoints, managed identity, RBAC, KV refs -->

## 💰 Impacto en costes

<!-- Estimación / link a calculadora de Azure -->

## 📐 Fase

<!-- phase-5-infra usualmente -->
