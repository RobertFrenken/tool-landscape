"""Tests for deps.dev collector parsing logic."""

from datetime import UTC, datetime

import duckdb

from landscape.analysis.collectors.deps_dev import (
    _build_tool_package_map,
    _get_package_versions,
)
from landscape.analysis.metrics import MetricRow, insert_metrics
from landscape.db.schema import create_schema


def _make_db() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DB with schema and test tools."""
    con = duckdb.connect(":memory:")
    create_schema(con)
    con.execute(
        "INSERT INTO tools (name, url, pypi_package, npm_package) "
        "VALUES ('tool-a', 'https://example.com', 'tool-a', NULL)"
    )
    con.execute(
        "INSERT INTO tools (name, url, pypi_package, npm_package) "
        "VALUES ('tool-b', 'https://example.com', NULL, 'tool-b')"
    )
    con.execute(
        "INSERT INTO tools (name, url, pypi_package, npm_package) "
        "VALUES ('tool-c', 'https://example.com', 'tool-c', NULL)"
    )
    return con


def test_build_tool_package_map():
    con = _make_db()
    mapping = _build_tool_package_map(con)
    con.close()

    assert "pypi/tool-a" in mapping
    assert "npm/tool-b" in mapping
    assert "pypi/tool-c" in mapping
    assert "npm/tool-a" not in mapping


def test_get_package_versions_from_metadata():
    con = _make_db()
    now = datetime(2026, 3, 11, tzinfo=UTC)

    # Insert pypi_metadata with a version
    tool_a_id = con.execute("SELECT tool_id FROM tools WHERE name = 'tool-a'").fetchone()[0]
    insert_metrics(
        con,
        [
            MetricRow(
                tool_a_id,
                "pypi_metadata",
                0.0,
                "pypi_stats",
                now,
                '{"version": "1.2.3", "requires_python": ">=3.9"}',
            )
        ],
    )

    packages = _get_package_versions(con)
    con.close()

    assert len(packages) == 1
    tool_id, system, name, version = packages[0]
    assert tool_id == tool_a_id
    assert system == "PYPI"
    assert name == "tool-a"
    assert version == "1.2.3"


def test_get_package_versions_empty_without_metrics():
    con = _make_db()
    packages = _get_package_versions(con)
    con.close()

    assert packages == []
