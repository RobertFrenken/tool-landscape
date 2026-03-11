"""DuckDB connection manager."""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "landscape.duckdb"


def connect(
    db_path: Path | str | None = None,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection.

    Args:
        db_path: Path to database file. None = in-memory (for tests).
        read_only: Open in read-only mode (for query commands).
    """
    if db_path is None:
        return duckdb.connect(":memory:")

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def get_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Get a connection to the default database."""
    return connect(DEFAULT_DB_PATH, read_only=read_only)
