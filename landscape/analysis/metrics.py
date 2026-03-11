"""Metric collection orchestrator.

Runs collectors against external APIs and bulk-inserts results into tool_metrics.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import NamedTuple

import duckdb

logger = logging.getLogger(__name__)


class MetricRow(NamedTuple):
    """A single metric measurement ready for insertion."""

    tool_id: int
    metric_name: str
    value: float
    source: str  # Must match metric_source enum
    measured_at: datetime
    metadata: str | None  # JSON string or None


def insert_metrics(con: duckdb.DuckDBPyConnection, rows: list[MetricRow]) -> int:
    """Bulk-insert metric rows, skipping duplicates."""
    if not rows:
        return 0

    count = 0
    for row in rows:
        try:
            con.execute(
                """
                INSERT INTO tool_metrics
                    (tool_id, metric_name, value, source, measured_at, metadata)
                VALUES ($1, $2, $3, $4::metric_source, $5, $6::JSON)
                ON CONFLICT (tool_id, metric_name, source, measured_at) DO UPDATE
                SET value = EXCLUDED.value, metadata = EXCLUDED.metadata
                """,
                list(row),
            )
            count += 1
        except duckdb.Error as e:
            logger.warning(
                "Failed to insert metric %s for tool_id=%d: %s", row.metric_name, row.tool_id, e
            )

    return count


def get_tools_with_identifiers(
    con: duckdb.DuckDBPyConnection,
    source: str | None = None,
    tool_names: list[str] | None = None,
) -> list[dict]:
    """Fetch tools with their registry identifiers for collection.

    Args:
        source: Filter to tools with identifiers for this source ('github', 'pypi', 'npm').
        tool_names: Filter to specific tool names.
    """
    conditions = []
    params: list = []
    idx = 1

    if source == "github":
        conditions.append("github_repo IS NOT NULL")
    elif source == "pypi":
        conditions.append("pypi_package IS NOT NULL")
    elif source == "npm":
        conditions.append("npm_package IS NOT NULL")

    if tool_names:
        placeholders = ", ".join(f"${i}" for i in range(idx, idx + len(tool_names)))
        conditions.append(f"name IN ({placeholders})")
        params.extend(tool_names)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT tool_id, name, github_repo, pypi_package, npm_package FROM tools{where}"  # noqa: S608

    rows = con.execute(query, params).fetchall()
    return [
        {
            "tool_id": r[0],
            "name": r[1],
            "github_repo": r[2],
            "pypi_package": r[3],
            "npm_package": r[4],
        }
        for r in rows
    ]


async def collect_all(
    con: duckdb.DuckDBPyConnection,
    sources: list[str] | None = None,
    tool_names: list[str] | None = None,
    github_token: str | None = None,
) -> dict[str, int]:
    """Run metric collection from all (or selected) sources.

    Returns:
        Dict of {source_name: rows_inserted}.
    """
    results: dict[str, int] = {}
    now = datetime.now(UTC)
    run_all = sources is None

    if run_all or "github" in (sources or []):
        from landscape.analysis.collectors.github import collect_github_metrics

        tools = get_tools_with_identifiers(con, source="github", tool_names=tool_names)
        if tools:
            logger.info("Collecting GitHub metrics for %d tools", len(tools))
            rows = await collect_github_metrics(tools, token=github_token, now=now)
            results["github"] = insert_metrics(con, rows)
        else:
            results["github"] = 0

    if run_all or "pypi" in (sources or []):
        from landscape.analysis.collectors.pypi import collect_pypi_metrics

        tools = get_tools_with_identifiers(con, source="pypi", tool_names=tool_names)
        if tools:
            logger.info("Collecting PyPI metrics for %d tools", len(tools))
            rows = await collect_pypi_metrics(tools, now=now)
            results["pypi"] = insert_metrics(con, rows)
        else:
            results["pypi"] = 0

    if run_all or "npm" in (sources or []):
        from landscape.analysis.collectors.npm import collect_npm_metrics

        tools = get_tools_with_identifiers(con, source="npm", tool_names=tool_names)
        if tools:
            logger.info("Collecting npm metrics for %d tools", len(tools))
            rows = await collect_npm_metrics(tools, now=now)
            results["npm"] = insert_metrics(con, rows)
        else:
            results["npm"] = 0

    return results


def run_collect(
    con: duckdb.DuckDBPyConnection,
    sources: list[str] | None = None,
    tool_names: list[str] | None = None,
    github_token: str | None = None,
) -> dict[str, int]:
    """Synchronous wrapper for collect_all."""
    return asyncio.run(
        collect_all(con, sources=sources, tool_names=tool_names, github_token=github_token)
    )
