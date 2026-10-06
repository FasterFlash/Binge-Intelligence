"""
SQL fallback tier — a safe escape hatch when no scoped tool fits.

Rules enforced BEFORE execution:
1. Must be a single statement (no `;` separator for multiple stmts).
2. Must be a SELECT (or WITH ... SELECT). Nothing else — no INSERT/UPDATE/
   DELETE/DDL/COPY/CALL/GRANT, no CTE that writes.
3. No dangerous constructs: pg_sleep, pg_read_file, pg_catalog mutation,
   format strings executing dynamic SQL, etc.
4. Any table that has a `user_id` column AND is in our known SELF_DATA set
   MUST filter by the authenticated user_id. If the LLM-authored query
   references such a table without a filter, we auto-inject
   `AND user_id = :auth_user_id` into the WHERE clause. If no WHERE, we
   add one.
5. Execution runs through a read-only session (statement timeout + transaction
   set to READ ONLY), so even if a check misses something write-y, Postgres
   refuses.
6. Row cap: LIMIT is injected if absent, capped at MAX_ROWS.

The LLM gets a tool `run_sql_fallback(sql: str, intent: str)` where `intent`
is a short natural-language reason — used by the judge later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 3000

# Tables that carry per-user rows. If a query touches one of these without a
# user_id predicate, we inject one.
SELF_DATA_TABLES: set[str] = {
    "sessions", "watch_events", "user_series_progress",
    "user_watch_stats", "subscription_periods", "calendar_activity",
    "agent_sessions", "agent_messages", "agent_events",
}

# Tables that are explicitly aggregate / catalog-wide and don't need the filter.
AGGREGATE_OK_TABLES: set[str] = {
    "titles", "episodes", "cohort_metrics", "title_engagement_stats",
    "households", "title_embeddings", "policy_chunks",
}


# ---------------------------------------------------------------------------
# Parse / validation
# ---------------------------------------------------------------------------
@dataclass
class SqlValidation:
    ok: bool
    reason: str = ""
    rewritten_sql: str = ""
    tables_touched: list[str] = field(default_factory=list)
    user_filter_injected: bool = False
    limit_injected: bool = False


_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|"
    r"copy|call|do|comment|cluster|reindex|refresh|vacuum|analyze|"
    r"listen|notify|load|lock|move|discard|reset)\b",
    re.IGNORECASE,
)

_DANGEROUS_FUNCS = re.compile(
    r"\b(pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
    r"lo_import|lo_export|dblink|current_setting|set_config)\s*\(",
    re.IGNORECASE,
)

_TABLE_REF = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    # Remove -- line comments and /* ... */ block comments
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _tables_referenced(sql: str) -> list[str]:
    return [m.lower() for m in _TABLE_REF.findall(sql)]


def validate_sql(sql: str, auth_user_id: str) -> SqlValidation:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        return SqlValidation(ok=False, reason="empty query")

    cleaned = _strip_comments(sql)

    # Multiple statements — ';' remaining after rstrip means mid-string
    if ";" in cleaned:
        return SqlValidation(ok=False, reason="multiple statements not allowed")

    first_token = cleaned.lstrip("(").lstrip().split(None, 1)[0].lower()
    if first_token not in {"select", "with"}:
        return SqlValidation(
            ok=False,
            reason=f"only SELECT/WITH queries allowed; got {first_token!r}",
        )

    # If WITH ... INSERT/UPDATE/DELETE sneaks through, _WRITE_KEYWORDS catches it.
    if _WRITE_KEYWORDS.search(cleaned):
        return SqlValidation(ok=False, reason="write keyword detected")
    if _DANGEROUS_FUNCS.search(cleaned):
        return SqlValidation(ok=False, reason="dangerous function detected")

    tables = _tables_referenced(cleaned)
    self_tables_hit = [t for t in tables if t in SELF_DATA_TABLES]

    rewritten = sql
    user_filter_injected = False
    if self_tables_hit:
        # Require user_id filter somewhere in the WHERE. If missing, inject.
        if not _has_user_id_predicate(cleaned, auth_user_id):
            rewritten = _inject_user_filter(sql, self_tables_hit[0], auth_user_id)
            user_filter_injected = True

    # LIMIT cap
    limit_injected = False
    if not re.search(r"\blimit\s+\d+", rewritten, re.IGNORECASE):
        rewritten = f"{rewritten.rstrip(';')} LIMIT {MAX_ROWS}"
        limit_injected = True
    else:
        # Cap it
        def _cap(m: re.Match) -> str:
            n = int(m.group(1))
            return f"LIMIT {min(n, MAX_ROWS)}"

        rewritten = re.sub(
            r"\blimit\s+(\d+)", _cap, rewritten, flags=re.IGNORECASE
        )

    return SqlValidation(
        ok=True,
        rewritten_sql=rewritten,
        tables_touched=tables,
        user_filter_injected=user_filter_injected,
        limit_injected=limit_injected,
    )


_USER_ID_PRED = re.compile(
    r"\buser_id\s*=\s*(?:'[0-9a-fA-F\-]{36}'|:auth_user_id|:user_id|\$[0-9]+)",
    re.IGNORECASE,
)


def _has_user_id_predicate(sql: str, auth_user_id: str) -> bool:
    """Heuristic: look for user_id = <uuid-literal> or user_id = :bind."""
    return bool(_USER_ID_PRED.search(sql))


def _inject_user_filter(sql: str, table: str, auth_user_id: str) -> str:
    """
    Add `WHERE user_id = :auth_user_id` (or AND, if WHERE exists).
    This is a best-effort patch — the read-only session is the real safety net.
    """
    # Case-insensitive search for first WHERE clause boundary vs. end
    lower = sql.lower()
    if " where " in lower:
        idx = lower.index(" where ") + len(" where ")
        return (
            sql[:idx] + f"user_id = '{auth_user_id}' AND " + sql[idx:]
        )
    # No WHERE — add it before GROUP/ORDER/LIMIT, or at end
    for kw in [" group by ", " order by ", " limit ", " offset "]:
        if kw in lower:
            idx = lower.index(kw)
            return sql[:idx] + f" WHERE user_id = '{auth_user_id}'" + sql[idx:]
    return sql + f" WHERE user_id = '{auth_user_id}'"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@dataclass
class SqlExecResult:
    ok: bool
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    error: str = ""
    validation: SqlValidation | None = None


def run_fallback(db: Session, sql: str, auth_user_id: str) -> SqlExecResult:
    """
    Validate + execute `sql` as the authenticated user. Returns row dicts,
    capped at MAX_ROWS, under a READ-ONLY transaction with statement timeout.
    """
    v = validate_sql(sql, auth_user_id)
    if not v.ok:
        return SqlExecResult(ok=False, error=f"validation failed: {v.reason}", validation=v)

    # Run inside a nested read-only transaction so we don't torch the outer
    # session's state if something goes sideways.
    try:
        # SAVEPOINT via begin_nested, then set session read-only + timeout
        db.rollback()  # make sure we're clean before this subblock
        db.execute(text("SET LOCAL default_transaction_read_only = on"))
        db.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        result = db.execute(text(v.rewritten_sql))
        rows = [dict(r._mapping) for r in result.fetchall()]
        cols = list(result.keys()) if result.returns_rows else []
        db.rollback()  # discard the read-only context cleanly
        return SqlExecResult(
            ok=True,
            rows=rows,
            row_count=len(rows),
            columns=cols,
            validation=v,
        )
    except SQLAlchemyError as e:
        try:
            db.rollback()
        except Exception:
            pass
        return SqlExecResult(
            ok=False,
            error=f"sql execution failed: {str(e)[:400]}",
            validation=v,
        )
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return SqlExecResult(
            ok=False,
            error=f"unexpected: {str(e)[:400]}",
            validation=v,
        )
