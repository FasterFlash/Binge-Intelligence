"""
DuckDB connection + Postgres attachment helpers.

DuckDB is embedded — this file just handles opening the project's local
.duckdb file and attaching Postgres as `pg` (read-only) so DuckDB queries
can hit raw tables as `pg.watch_events`, `pg.users`, etc.
"""

from __future__ import annotations

import os
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parent.parent.parent
DUCKDB_DIR = ROOT / "duckdb"
DUCKDB_PATH = DUCKDB_DIR / "binge_intel.duckdb"
PARQUET_DIR = DUCKDB_DIR / "parquet"


def postgres_libpq_dsn() -> str:
    """Convert a SQLAlchemy-style DATABASE_URL into a libpq connection string."""
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence",
    )
    p = urllib.parse.urlparse(url)
    return (
        f"host={p.hostname} port={p.port or 5432} "
        f"user={p.username} password={p.password} dbname={p.path.lstrip('/')}"
    )


@contextmanager
def duckdb_connected(read_only_pg: bool = True):
    """
    Open DuckDB connection, install + load the postgres extension,
    attach Postgres as `pg`, yield the connection.
    """
    DUCKDB_DIR.mkdir(parents=True, exist_ok=True)
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        con.execute("INSTALL postgres;")
        con.execute("LOAD postgres;")
        # detach any previous pg attachment (ignore error if not attached)
        try:
            con.execute("DETACH pg;")
        except Exception:
            pass
        dsn = postgres_libpq_dsn()
        mode = "READ_ONLY" if read_only_pg else "READ_WRITE"
        con.execute(f"ATTACH '{dsn}' AS pg (TYPE POSTGRES, {mode});")
        yield con
    finally:
        try:
            con.execute("DETACH pg;")
        except Exception:
            pass
        con.close()