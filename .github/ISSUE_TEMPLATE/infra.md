---
name: "⚙️ Infrastructure"
about: "Changes to Bicep, azd, APIM, Container Apps, networking"
title: "[infra] "
labels: ["⚙️ infra", "🏛️ infra"]
assignees: []
---

## ⚙️ Description

<!-- Which resource/configuration changes? -->

## 🎯 Motivation

<!-- Problem it solves. -->

## 📋 Acceptance criteria

- [ ] Bicep compiles with no warnings
- [ ] `az deployment ... validate` OK
- [ ] `what-if` reviewed
- [ ] Estimated cost documented if it is a new resource
- [ ] Minimum RBAC applied (no Owner / Contributor blanket)
- [ ] Secrets in KV, never in Bicep parameters
- [ ] `infra-validate` workflow green

## 🗂️ Files to touch

<!-- e.g. infra/main.bicep, infra/modules/apim.bicep -->

## 🔐 Security considerations

<!-- Public endpoints, managed identity, RBAC, KV refs -->

## 💰 Cost impact

<!-- Estimate / link to the Azure calculator -->

## 📐 Phase

<!-- usually phase-5-infra -->
