# database/scripts/

Self-contained Python utilities owned by the data team. The scripts here
do **not** import from `apps/mcp` or `apps/backend` — they ship with
their own `pyproject.toml` so they can be installed in a CI job that
does not need the full agent stack.

## `check_metadata_drift.py`

Read-only drift checker between `_metadata.catalog_*` and
`INFORMATION_SCHEMA`. See the top-of-module docstring for the full
rationale (PLAN.md §9 / Decision D9). Output is the JSON shape
documented in [`../README.md`](../README.md#drift-check) and the
exit code is `0` when clean, `1` on drift, `2` on bad environment.

```bash
cd database/scripts
pip install -e .[dev]
python -m pytest --cov=. --cov-report=term -v
python -m ruff check .
```
