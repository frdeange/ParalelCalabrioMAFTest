# DevOps Setup

> Operational guide for collaborating on this repository following DevOps best practices.
> **The model is described in [PLAN.md §12](../PLAN.md#12-devops-and-branching).** This doc is the practical implementation.

---

## 1. Branching model (summary)

```
main          ●─────●─────●         (protected, production)
              ↑     ↑     ↑
develop       ●──●──●──●──●         (protected, integration)
                 ↑  ↑     ↑
feature/*       ●──●     ●          (ephemeral)
```

- `main` ← merge only from `develop` (PR + review + green CI).
- `develop` ← merge only from `feature/*`, `fix/*`, `docs/*`, `chore/*` (PR + green CI).
- All new work is born from `develop`. **Never** work directly on `main` or `develop`.

---

## 2. Configure branch protection in the GitHub UI

> This **must** be done by the repo owner manually. The API can automate it but requires a PAT with `repo` + `admin:repo_hook` scope — easier to do in the UI.

### Steps

1. Go to https://github.com/frdeange/ParalelCalabrioMAFTest/settings/branches
2. Click **"Add branch ruleset"** (or "Add rule" in the classic version).

### Rule for `main`

| Setting | Value |
|---------|-------|
| Branch name pattern | `main` |
| Restrict deletions | ✅ ON |
| Require linear history | ✅ ON |
| Require a pull request before merging | ✅ ON |
| → Required approvals | `0` (solo project) or `1` if collaborators exist |
| → Dismiss stale PR approvals | ✅ ON |
| → Require review from Code Owners | ✅ ON (once `CODEOWNERS` exists) |
| Require status checks to pass | ✅ ON |
| → Required checks | `backend-ci`, `frontend-ci`, `mcp-ci`, `infra-validate` |
| → Require branches to be up to date | ✅ ON |
| Require conversation resolution | ✅ ON |
| Block force pushes | ✅ ON |

### Rule for `develop`

Same configuration but:
- Required approvals: `0` (in a solo project) — faster merge.
- Require linear history: ✅ ON (squash merges produce linear history).
- Everything else identical.

### Auto-delete head branches (recommended)

`Settings → General → Pull Requests → Automatically delete head branches` ✅ ON.
`feature/*` branches are deleted automatically on merge.

---

## 3. Daily workflow

### Start a new piece of work

```bash
# 1. sync develop
git checkout develop
git pull origin develop

# 2. feature branch
git checkout -b feature/<phase>-<short-desc>
# e.g. feature/phase-1-backend-scaffold

# 3. work, commit
git add .
git commit -m "feat(backend): scaffold FastAPI ag-ui endpoint"

# 4. push
git push -u origin feature/phase-1-backend-scaffold

# 5. open PR via gh CLI
gh pr create --base develop --title "feat(backend): scaffold ag-ui endpoint" --body "Closes #12"
```

### Conventional commits (required)

```
<type>(<scope>): <description>

[optional body]

[optional footer: Closes #N]
```

Allowed types:
- `feat` — new functionality
- `fix` — bugfix
- `docs` — documentation only
- `refactor` — refactor with no behavior change
- `test` — add/change tests
- `chore` — maintenance (deps, config)
- `ci` — workflow changes
- `perf` — performance
- `security` — security fix

Suggested scopes: `backend`, `frontend`, `mcp`, `infra`, `apim`, `db`, `docs`, `ci`.

Examples:
- `feat(backend): add HMAC verification dependency`
- `fix(mcp): handle empty result set in query.execute`
- `docs(plan): clarify BU resolution layer order`

### Branch naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/<phase>-<desc>` | `feature/phase-2-mcp-validator` |
| Fix (non-prod) | `fix/<issue-id>-<desc>` | `fix/42-mcp-timeout` |
| Docs | `docs/<topic>` | `docs/adr-0002` |
| Chore | `chore/<topic>` | `chore/bump-deps` |
| Hotfix (prod) | `hotfix/<issue-id>` | `hotfix/99-apim-policy-bug` |

### Hotfix path (exception)

If production breaks and we cannot wait for the `develop` cycle:

```bash
git checkout main
git pull
git checkout -b hotfix/<id>
# fix
git commit -m "fix: ..."
gh pr create --base main          # PR to main
# after merge:
git checkout develop
git merge main                    # backport
git push origin develop
```

---

## 4. Pull Requests

### Mandatory checklist (in the template)

- [ ] Tests pass locally
- [ ] New tests added where applicable
- [ ] Docs updated (`PLAN.md` if architecture changes, component README if API changes)
- [ ] Issue referenced in body (`Closes #N`)
- [ ] CI green
- [ ] No secrets in commits

### Reviewers

As a solo learning project, the owner self-reviews. **Even so**:
1. Keep PRs small and atomic (1 issue = 1 PR, max ~400 LOC changed).
2. Wait 24h before merging non-trivial PRs (review with a fresh head).
3. If CI fails, **do not merge with override** — understand the failure.

### Squash vs merge commit

- PR `feature → develop` → **squash and merge** (cleans WIP commits).
- PR `develop → main` → **merge commit** (preserves individual features as release references).

---

## 5. Issues

### When to open an issue

Any work > 30 minutes. Less than that, a direct commit with a good message is fine.

### Structure

Every issue carries:
- **Type label** (1): `🐛 bug`, `✨ feature`, `📚 docs`, etc.
- **Component label** (1): `🧠 backend`, `🎨 frontend`, `🔌 mcp`, `🏛️ infra`, `📦 cross-cutting`.
- **Phase label** (0..1): `🏗️ phase-0-scaffold`, `🔧 phase-1-backend`, etc.
- **Priority** (1): `🔴 critical`, `🟠 high`, `🟡 medium`, `🟢 low`.
- Optional **Status**: `🚧 blocked`, `👀 needs-review`, `⚡ parallelizable`.

### Template

See [.github/ISSUE_TEMPLATE/](../.github/ISSUE_TEMPLATE/) — feature, bug, docs, infra, security, test, refactor, chore.

---

## 6. CI/CD

### Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `backend-ci.yml` | PR / push to `apps/backend/**` | lint (ruff) + pytest + docker build |
| `frontend-ci.yml` | PR / push to `apps/frontend/**` | lint (eslint) + vitest + next build |
| `mcp-ci.yml` | PR / push to `apps/mcp/**` | lint (ruff) + pytest + docker build |
| `infra-validate.yml` | PR / push to `infra/**` | bicep build + lint + what-if (no apply) |
| `e2e-tests.yml` | push to `develop` post-merge | Full Playwright suite |

### Path filters

Each workflow uses `paths:` so it runs only when its component changes. Significantly reduces CI time.

### Required secrets (GitHub repo settings → Secrets and variables → Actions)

- `AZURE_CREDENTIALS` (federated identity preferred)
- `ACR_USERNAME` / `ACR_PASSWORD` (if not using federated)
- `HMAC_SECRET_TEST` (for integration tests)

---

## 7. Releases

### Versioning

SemVer (`vMAJOR.MINOR.PATCH`):
- `MAJOR` — backwards-incompatible public API changes (rare).
- `MINOR` — features.
- `PATCH` — fixes.

### Tagging

Only on `main`, after a merge from `develop` that represents a release:

```bash
git checkout main
git pull
git tag -a v0.1.0 -m "Phase 0-2 complete: backend + mcp scaffold"
git push origin v0.1.0
```

### CHANGELOG

Maintain `CHANGELOG.md` in [Keep a Changelog](https://keepachangelog.com/) style.

---

## 8. Onboarding a new collaborator

Manual steps:
1. Add as collaborator on the repo (Settings → Collaborators).
2. Share Azure subscription access if they will deploy.
3. Share Foundry project access if they will touch agents.
4. Add their entry in `CODEOWNERS` if they become an owner of a component.
5. Share `.env.example` and secrets through a secure channel.

---

**Last updated**: 2026-05-27
