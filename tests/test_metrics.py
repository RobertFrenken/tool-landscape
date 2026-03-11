"""Tests for metrics orchestrator and collectors."""

from datetime import UTC, datetime

import duckdb

from landscape.analysis.collectors.github import _parse_repo_data
from landscape.analysis.metrics import MetricRow, insert_metrics
from landscape.db.schema import create_schema


def _make_db() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DB with schema and one test tool."""
    con = duckdb.connect(":memory:")
    create_schema(con)
    con.execute(
        "INSERT INTO tools (name, url, github_repo, pypi_package) "
        "VALUES ('test-tool', 'https://example.com', 'org/repo', 'test-tool')"
    )
    return con


def test_insert_metrics_basic():
    con = _make_db()
    tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'test-tool'").fetchone()[0]
    now = datetime(2026, 3, 11, tzinfo=UTC)

    rows = [
        MetricRow(tool_id, "github_stars", 1000.0, "github_api", now, None),
        MetricRow(tool_id, "pypi_downloads_monthly", 50000.0, "pypi_stats", now, None),
    ]
    count = insert_metrics(con, rows)
    assert count == 2

    total = con.execute("SELECT count(*) FROM tool_metrics").fetchone()[0]
    assert total == 2
    con.close()


def test_insert_metrics_upsert():
    con = _make_db()
    tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'test-tool'").fetchone()[0]
    now = datetime(2026, 3, 11, tzinfo=UTC)

    rows = [MetricRow(tool_id, "github_stars", 1000.0, "github_api", now, None)]
    insert_metrics(con, rows)

    # Insert again with updated value — should upsert
    rows = [MetricRow(tool_id, "github_stars", 1500.0, "github_api", now, None)]
    insert_metrics(con, rows)

    total = con.execute("SELECT count(*) FROM tool_metrics").fetchone()[0]
    assert total == 1

    value = con.execute(
        "SELECT value FROM tool_metrics WHERE metric_name = 'github_stars'"
    ).fetchone()[0]
    assert value == 1500.0
    con.close()


def test_parse_github_repo_data():
    now = datetime(2026, 3, 11, tzinfo=UTC)
    data = {
        "stargazerCount": 15000,
        "forkCount": 1200,
        "pushedAt": "2026-03-10T12:00:00Z",
        "isArchived": False,
        "licenseInfo": {"spdxId": "Apache-2.0"},
        "issues": {"totalCount": 150},
        "releases": {"nodes": [{"publishedAt": "2026-02-01T00:00:00Z"}]},
        "defaultBranchRef": {"target": {"history": {"totalCount": 8000}}},
    }

    rows = _parse_repo_data(tool_id=1, data=data, now=now)
    metric_names = [r.metric_name for r in rows]

    assert "github_stars" in metric_names
    assert "github_forks" in metric_names
    assert "github_open_issues" in metric_names
    assert "github_last_push_days_ago" in metric_names
    assert "github_last_release_days_ago" in metric_names
    assert "github_total_commits" in metric_names
    assert "github_metadata" in metric_names

    stars = next(r for r in rows if r.metric_name == "github_stars")
    assert stars.value == 15000.0
