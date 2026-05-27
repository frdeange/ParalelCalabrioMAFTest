---
name: "♻️ Refactor"
about: "Cambio interno sin alterar comportamiento"
title: "[refactor] "
labels: ["♻️ refactor"]
assignees: []
---

## ♻️ ¿Qué queremos refactorizar?

<!-- Módulo, clase, función. -->

## 🎯 Motivación

<!-- Smell, duplicación, complejidad, performance, legibilidad. -->

## ✅ Garantías de no-regresión

- [ ] Tests existentes pasan sin modificación
- [ ] Si hay cambios de tests, son aditivos (no relajan asserts)
- [ ] No cambia API pública del módulo (o se documenta migración)

## 🗂️ Archivos a tocar

<!-- ej. apps/backend/app/workflow.py -->

## 📐 Antes / Después (boceto)

```python
# antes

# después
```
