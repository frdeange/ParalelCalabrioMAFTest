"""SQL validator — ``sqlglot``-backed AST gate for ``query.execute``.

Issue: #19 ``[phase-2][mcp] sqlglot-based query.validate (AST allowlist
+ injection blocks)``.

Purpose
-------
Two threats converge on the same chokepoint:

1. **SQL injection** — an LLM-built query reaching the database with
   unintended privileges. The validator rejects anything that is not a
   plain ``SELECT`` (or CTE / set-operation built on top of one).
2. **Over-eager mutations** — an LLM "helping" by issuing
   ``UPDATE``/``DELETE``/``MERGE``/``DROP``/etc. The validator rejects
   every DML/DDL/EXEC/transaction/control-flow node anywhere in the
   tree.

The validator is the *first* of three layers (PLAN.md §11 security):

1. This AST gate.
2. The read-only Azure SQL grant on ``uai_readonly`` (`db_datareader`
   + custom narrow grants — see ``database/04-grant-readonly.sql``).
3. The forced ``WHERE bu_id = @bu_id`` injection done by
   ``query.execute`` (#20).

Design notes
------------
* **Pure / stateless** — takes a SQL string and an optional allowlist;
  performs no I/O. The caller (e.g. ``query.execute``) is responsible
  for materialising the allowlist (typically from
  ``_metadata.catalog_tables`` where ``is_active = 1`` — see PLAN.md §9
  Decision D9; the issue body still mentions the older
  ``_metadata.agent_allowlist`` name).
* **AST-only checks** — we never inspect the raw string for keywords.
  String-based blocklists are trivially bypassed with comments or
  Unicode look-alikes; the tokenizer-driven AST is not.
* **Comment-stripping bypass coverage** — the classic
  ``SELECT 1 /* anything */; DROP TABLE x`` is rejected by the
  one-statement rule (``sqlglot.parse`` returns two top-level nodes).
  Within a single statement, comments are tokenised separately and
  removed from the normalised output so they cannot smuggle SQL into
  downstream consumers (audit logs, error messages).
* **Dialect** — ``tsql``. Azure SQL is the only target backend
  (PLAN.md §6.3, §8). Using the right dialect matters for ``TOP n`` vs
  ``LIMIT n`` and for ``[bracketed identifiers]``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

_DIALECT: Final = "tsql"

# Top-level statement nodes the agent may submit. Note that
# ``WITH cte AS (...) SELECT ...`` surfaces as a plain :class:`exp.Select`
# in sqlglot 30.x (the CTE list is parked under ``Select.args["with"]``),
# so :class:`exp.With` is rarely seen at the top — we list it anyway
# for forward-compatibility with future sqlglot versions.
_ALLOWED_TOP_LEVEL: Final[tuple[type[exp.Expression], ...]] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
    exp.Subquery,
    exp.With,
)

# AST nodes that either mutate state or invoke arbitrary code paths.
# The validator rejects any tree containing any of these, anywhere
# (top-level *or* embedded). The list is intentionally over-broad:
# when in doubt, block.
#
# Coverage rationale:
#   - DML  : Insert/Update/Delete/Merge       — mutate rows
#   - DDL  : Create/Drop/Alter/TruncateTable  — mutate schema
#   - AC   : Grant                            — escalate privileges
#   - CTX  : Use                              — switch database
#   - VARS : Set/Declare                      — T-SQL scripting
#   - CTRL : IfBlock                          — conditional execution
#   - TX   : Transaction/Commit/Rollback      — transaction control
#   - EXEC : Execute/Command                  — stored proc / unsupported
#                                               (sqlglot maps EXEC to
#                                               Execute and falls back
#                                               to Command for KILL,
#                                               BACKUP, RESTORE, etc.)
_FORBIDDEN: Final[tuple[type[exp.Expression], ...]] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Use,
    exp.Set,
    exp.Declare,
    exp.IfBlock,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Execute,
    exp.Command,
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of :func:`validate`.

    Attributes
    ----------
    ok:
        ``True`` if the SQL passes every check; ``False`` otherwise.
    reason:
        Human-readable explanation when ``ok`` is ``False``; ``None``
        when ``ok`` is ``True``. Safe to surface in tool errors —
        contains no untrusted SQL fragments other than identifier
        names already supplied by the caller.
    normalized_sql:
        AST-rendered SQL with comments stripped (``tsql`` dialect).
        ``None`` when ``ok`` is ``False``. ``query.execute`` should
        send this normalised form to ``SqlDatabaseClient`` rather than
        the original string, so audit logs (PLAN.md §9
        ``_metadata.tool_audit.sql_text``) match what actually ran.
    tables:
        Deduplicated, order-preserving tuple of table references
        extracted from the AST, in the form seen in the query
        (``schema.table`` when schema-qualified, bare ``table`` when
        not). CTE self-references are excluded. ``()`` when ``ok`` is
        ``False``.
    """

    ok: bool
    reason: str | None = None
    normalized_sql: str | None = None
    tables: tuple[str, ...] = ()


def validate(
    sql: str,
    allowlist: Iterable[str] | None = None,
) -> ValidationResult:
    """Validate a single read-only ``SELECT`` statement.

    Parameters
    ----------
    sql:
        Raw SQL string. Must contain exactly one parseable statement.
    allowlist:
        Optional iterable of allowed table names. Matching is
        case-insensitive and accepts both ``schema.table`` and bare
        ``table`` entries (a bare entry matches references with *any*
        schema; a qualified entry also matches bare references with
        the same table name). Pass ``None`` to skip the allowlist
        check entirely — useful for tests and for early scaffolding,
        but production callers (e.g. ``query.execute``) must always
        supply one sourced from ``_metadata.catalog_tables``.

    Returns
    -------
    ValidationResult
        See class docstring. ``ok`` is ``True`` iff:

        * exactly one parseable statement was found,
        * its top-level node is in :data:`_ALLOWED_TOP_LEVEL`,
        * no node anywhere in the tree is in :data:`_FORBIDDEN`,
        * (when ``allowlist`` is provided) every referenced table is
          in the allowlist.
    """
    if not isinstance(sql, str):
        return ValidationResult(
            ok=False,
            reason=f"sql must be a str, got {type(sql).__name__}",
        )
    if not sql.strip():
        return ValidationResult(ok=False, reason="sql must be non-empty")

    try:
        parsed = sqlglot.parse(sql, dialect=_DIALECT)
    except (ParseError, TokenError) as exc:
        return ValidationResult(ok=False, reason=f"parse error: {exc}")

    # ``sqlglot.parse`` can return ``None`` entries for empty segments
    # between semicolons (e.g. ``";"`` or ``"; SELECT 1"``). Drop them
    # before counting statements so a leading/trailing semicolon does
    # not look like a multi-statement attempt.
    statements = [s for s in parsed if s is not None]
    if not statements:
        return ValidationResult(ok=False, reason="no parseable statement found")
    if len(statements) > 1:
        return ValidationResult(
            ok=False,
            reason=(
                "multi-statement queries are not allowed "
                f"(found {len(statements)} statements)"
            ),
        )

    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_TOP_LEVEL):
        return ValidationResult(
            ok=False,
            reason=(
                "top-level statement must be SELECT/CTE/UNION/INTERSECT/EXCEPT, "
                f"got {type(stmt).__name__}"
            ),
        )

    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN):
            return ValidationResult(
                ok=False,
                reason=f"forbidden statement: {type(node).__name__}",
            )

    tables = _extract_tables(stmt)

    if allowlist is not None:
        denied = _first_table_not_in_allowlist(tables, allowlist)
        if denied is not None:
            return ValidationResult(
                ok=False,
                reason=f"table {denied!r} is not in the agent allowlist",
            )

    _strip_comments(stmt)
    normalized = stmt.sql(dialect=_DIALECT)
    return ValidationResult(
        ok=True,
        reason=None,
        normalized_sql=normalized,
        tables=tuple(tables),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_tables(stmt: exp.Expression) -> list[str]:
    """Return distinct table references in ``stmt``, excluding CTEs.

    Preserves first-seen order so error messages and audit entries
    stay deterministic.
    """
    cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE) if cte.alias}
    tables: list[str] = []
    seen: set[str] = set()
    for table in stmt.find_all(exp.Table):
        bare = table.name
        if not bare:
            continue
        if bare.lower() in cte_names:
            continue
        db = table.args.get("db")
        schema = db.name if db is not None and getattr(db, "name", None) else None
        qualified = f"{schema}.{bare}" if schema else bare
        key = qualified.lower()
        if key in seen:
            continue
        seen.add(key)
        tables.append(qualified)
    return tables


def _first_table_not_in_allowlist(
    tables: Iterable[str],
    allowlist: Iterable[str],
) -> str | None:
    """Return the first table not present in ``allowlist`` (or ``None``).

    Matching is case-insensitive. An allowlist entry of ``"users"``
    matches any reference whose bare name is ``users`` (with or
    without schema). An entry of ``"dbo.users"`` additionally
    populates a bare-name shortcut, so ``"users"`` in the SQL also
    matches.
    """
    allow: set[str] = set()
    for entry in allowlist:
        entry_lower = entry.lower()
        allow.add(entry_lower)
        if "." in entry_lower:
            allow.add(entry_lower.split(".", 1)[-1])

    for table in tables:
        tl = table.lower()
        bare = tl.split(".", 1)[-1] if "." in tl else tl
        if tl not in allow and bare not in allow:
            return table
    return None


def _strip_comments(node: exp.Expression) -> None:
    """Recursively clear ``.comments`` on ``node`` and its descendants.

    ``sqlglot`` tokenises ``--`` and ``/* ... */`` comments separately
    from the SQL grammar and reattaches them to the nearest AST node.
    Calling :meth:`Expression.sql` then re-emits them. The validator
    walks the whole subtree and drops every comment so the normalised
    output stored in audit logs is the SQL that *actually* ran.
    """
    for n in node.walk():
        if n.comments:
            n.comments = None


__all__ = ["ValidationResult", "validate"]
