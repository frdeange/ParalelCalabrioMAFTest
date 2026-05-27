# DevOps Setup

> Guía operativa para colaborar en este repositorio con buenas prácticas DevOps.
> **El modelo está descrito en [PLAN.md §12](../PLAN.md#12-devops-y-branching).** Este doc es la implementación práctica.

---

## 1. Modelo de branching (resumen)

```
main          ●─────●─────●         (protected, production)
              ↑     ↑     ↑
develop       ●──●──●──●──●         (protected, integration)
                 ↑  ↑     ↑
feature/*       ●──●     ●          (ephemeral)
```

- `main` ← merge solo desde `develop` (PR + review + CI verde).
- `develop` ← merge solo desde `feature/*`, `fix/*`, `docs/*`, `chore/*` (PR + CI verde).
- Cualquier trabajo nuevo nace de `develop`. **Nunca** se trabaja directamente en `main` ni `develop`.

---

## 2. Configurar branch protection en GitHub UI

> Esto **debe** hacerlo el owner del repo manualmente. La API permite automatizarlo pero requiere PAT con scope `repo` + `admin:repo_hook` — más fácil hacerlo en UI.

### Pasos

1. Ir a https://github.com/frdeange/ParalelCalabrioMAFTest/settings/branches
2. Clic en **"Add branch ruleset"** (o "Add rule" si está en versión clásica)

### Regla para `main`

| Setting | Valor |
|---------|-------|
| Branch name pattern | `main` |
| Restrict deletions | ✅ ON |
| Require linear history | ✅ ON |
| Require a pull request before merging | ✅ ON |
| → Required approvals | `0` (proyecto personal) o `1` si hay colaboradores |
| → Dismiss stale PR approvals | ✅ ON |
| → Require review from Code Owners | ✅ ON (cuando exista `CODEOWNERS`) |
| Require status checks to pass | ✅ ON |
| → Required checks | `backend-ci`, `frontend-ci`, `mcp-ci`, `infra-validate` |
| → Require branches to be up to date | ✅ ON |
| Require conversation resolution | ✅ ON |
| Block force pushes | ✅ ON |

### Regla para `develop`

Misma configuración pero:
- Required approvals: `0` (en proyecto unipersonal) — más rápido el merge.
- Require linear history: ✅ ON (squash merges generan historia lineal).
- Resto idéntico.

### Auto-delete head branches (recomendado)

`Settings → General → Pull Requests → Automatically delete head branches` ✅ ON.
Las branches `feature/*` se borran solas al hacer merge.

---

## 3. Workflow diario

### Empezar un trabajo nuevo

```bash
# 1. sync develop
git checkout develop
git pull origin develop

# 2. branch feature
git checkout -b feature/<phase>-<short-desc>
# ej. feature/phase-1-backend-scaffold

# 3. trabajar, commitear
git add .
git commit -m "feat(backend): scaffold FastAPI ag-ui endpoint"

# 4. push
git push -u origin feature/phase-1-backend-scaffold

# 5. PR via gh CLI
gh pr create --base develop --title "feat(backend): scaffold ag-ui endpoint" --body "Closes #12"
```

### Conventional commits (obligatorios)

```
<type>(<scope>): <description>

[body opcional]

[footer opcional: Closes #N]
```

Types permitidos:
- `feat` — nueva funcionalidad
- `fix` — bugfix
- `docs` — solo docs
- `refactor` — refactor sin cambio de comportamiento
- `test` — añadir/cambiar tests
- `chore` — mantenimiento (deps, config)
- `ci` — cambios en workflows
- `perf` — performance
- `security` — fix de seguridad

Scopes sugeridos: `backend`, `frontend`, `mcp`, `infra`, `apim`, `db`, `docs`, `ci`.

Ejemplos:
- `feat(backend): add HMAC verification dependency`
- `fix(mcp): handle empty result set in query.execute`
- `docs(plan): clarify BU resolution layer order`

### Naming de branches

| Tipo | Pattern | Ejemplo |
|------|---------|---------|
| Feature | `feature/<phase>-<desc>` | `feature/phase-2-mcp-validator` |
| Fix (no-prod) | `fix/<issue-id>-<desc>` | `fix/42-mcp-timeout` |
| Docs | `docs/<topic>` | `docs/adr-0002` |
| Chore | `chore/<topic>` | `chore/bump-deps` |
| Hotfix (prod) | `hotfix/<issue-id>` | `hotfix/99-apim-policy-bug` |

### Hotfix path (excepción)

Si producción rompe y no se puede esperar al ciclo `develop`:

```bash
git checkout main
git pull
git checkout -b hotfix/<id>
# arreglar
git commit -m "fix: ..."
gh pr create --base main          # PR a main
# tras merge:
git checkout develop
git merge main                    # backport
git push origin develop
```

---

## 4. Pull Requests

### Checklist obligatorio (en el template)

- [ ] Tests pasan localmente
- [ ] Nuevos tests añadidos donde aplica
- [ ] Docs actualizadas (`PLAN.md` si cambia arquitectura, README del componente si cambia API)
- [ ] Issue referenciado en el body (`Closes #N`)
- [ ] CI verde
- [ ] No secrets en commits

### Reviewers

Como proyecto unipersonal de aprendizaje, el owner se auto-revisa. **Aun así**:
1. PR pequeño y atómico (1 issue = 1 PR, máx ~400 LOC cambiadas).
2. Esperar 24h antes de mergear PRs no-triviales (para revisar con cabeza fresca).
3. Si CI rompe, **no mergear con override** — entender el fallo.

### Squash vs merge commit

- PR `feature → develop` → **squash and merge** (limpia commits WIP).
- PR `develop → main` → **merge commit** (preserva las features individuales como referencias del release).

---

## 5. Issues

### Cuándo crear un issue

Cualquier trabajo > 30 minutos. Si es menos, commit directo con buen mensaje.

### Estructura

Cada issue lleva:
- **Type label** (1): `🐛 bug`, `✨ feature`, `📚 docs`, etc.
- **Component label** (1): `🧠 backend`, `🎨 frontend`, `🔌 mcp`, `🏛️ infra`, `📦 cross-cutting`.
- **Phase label** (0..1): `🏗️ phase-0-scaffold`, `🔧 phase-1-backend`, etc.
- **Priority** (1): `🔴 critical`, `🟠 high`, `🟡 medium`, `🟢 low`.
- **Status** opcional: `🚧 blocked`, `👀 needs-review`, `⚡ parallelizable`.

### Template

Ver [.github/ISSUE_TEMPLATE/](../.github/ISSUE_TEMPLATE/) — feature, bug, docs, infra, security, test, refactor, chore.

---

## 6. CI/CD

### Workflows

| Workflow | Trigger | Qué hace |
|----------|---------|----------|
| `backend-ci.yml` | PR / push a `apps/backend/**` | lint (ruff) + pytest + build docker |
| `frontend-ci.yml` | PR / push a `apps/frontend/**` | lint (eslint) + vitest + build next |
| `mcp-ci.yml` | PR / push a `apps/mcp/**` | lint (ruff) + pytest + build docker |
| `infra-validate.yml` | PR / push a `infra/**` | bicep build + lint + what-if (no apply) |
| `e2e-tests.yml` | push a `develop` post-merge | Playwright full suite |

### Path filters

Cada workflow tiene `paths:` para correr solo cuando cambia su componente. Reduce tiempo de CI considerablemente.

### Secrets necesarios (GitHub repo settings → Secrets and variables → Actions)

- `AZURE_CREDENTIALS` (federated identity preferido)
- `ACR_USERNAME` / `ACR_PASSWORD` (si no se usa federated)
- `HMAC_SECRET_TEST` (para tests de integración)

---

## 7. Releases

### Versionado

SemVer (`vMAJOR.MINOR.PATCH`):
- `MAJOR` — cambios incompatibles en API pública (raros).
- `MINOR` — features.
- `PATCH` — fixes.

### Tagging

Solo en `main`, tras un merge desde `develop` que represente un release:

```bash
git checkout main
git pull
git tag -a v0.1.0 -m "Phase 0-2 complete: backend + mcp scaffold"
git push origin v0.1.0
```

### CHANGELOG

Mantener `CHANGELOG.md` al estilo [Keep a Changelog](https://keepachangelog.com/).

---

## 8. Onboarding de un colaborador nuevo

Pasos manuales:
1. Añadir como collaborator al repo (Settings → Collaborators).
2. Compartir acceso a la sub Azure si va a deployar.
3. Compartir Foundry project si va a tocar agentes.
4. Crear su entrada en `CODEOWNERS` si va a ser owner de un componente.
5. Compartir `.env.example` y secretos via canal seguro.

---

**Última actualización**: 2026-05-27
