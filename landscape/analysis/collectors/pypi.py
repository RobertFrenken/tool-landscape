"""PyPI metadata + download stats collector."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx

from landscape.analysis.metrics import MetricRow

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/{package}/json"
PYPISTATS_URL = "https://pypistats.org/api/packages/{package}/recent"


async def _fetch_pypi_metadata(
    client: httpx.AsyncClient,
    tool_id: int,
    package: str,
    now: datetime,
) -> list[MetricRow]:
    """Fetch metadata from PyPI JSON API."""
    rows: list[MetricRow] = []
    try:
        resp = await client.get(PYPI_URL.format(package=package), follow_redirects=True)
        if resp.status_code != 200:
            return rows
        data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("PyPI metadata fetch failed for %s: %s", package, e)
        return rows

    info = data.get("info", {})
    meta = {
        "version": info.get("version"),
        "requires_python": info.get("requires_python"),
        "license": info.get("license"),
    }
    rows.append(MetricRow(tool_id, "pypi_metadata", 0.0, "pypi_stats", now, json.dumps(meta)))

    return rows


async def _fetch_pypi_downloads(
    client: httpx.AsyncClient,
    tool_id: int,
    package: str,
    now: datetime,
) -> list[MetricRow]:
    """Fetch recent download stats from pypistats.org."""
    rows: list[MetricRow] = []
    try:
        resp = await client.get(PYPISTATS_URL.format(package=package), follow_redirects=True)
        if resp.status_code == 429:
            logger.info("pypistats 429 for %s, retrying after 2s", package)
            await asyncio.sleep(2.0)
            resp = await client.get(PYPISTATS_URL.format(package=package), follow_redirects=True)
        if resp.status_code != 200:
            return rows
        data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("pypistats fetch failed for %s: %s", package, e)
        return rows

    # pypistats returns {data: {last_day, last_week, last_month}}
    stats = data.get("data", {})
    if stats.get("last_month"):
        rows.append(
            MetricRow(
                tool_id,
                "pypi_downloads_monthly",
                float(stats["last_month"]),
                "pypi_stats",
                now,
                None,
            )
        )
    if stats.get("last_week"):
        rows.append(
            MetricRow(
                tool_id, "pypi_downloads_weekly", float(stats["last_week"]), "pypi_stats", now, None
            )
        )

    return rows


async def collect_pypi_metrics(
    tools: list[dict],
    *,
    now: datetime | None = None,
) -> list[MetricRow]:
    """Collect PyPI metrics for tools that have pypi_package set."""
    now = now or datetime.now(UTC)
    all_rows: list[MetricRow] = []

    valid = [(t["tool_id"], t["pypi_package"]) for t in tools if t.get("pypi_package")]
    if not valid:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, (tool_id, package) in enumerate(valid):
            meta_rows = await _fetch_pypi_metadata(client, tool_id, package, now)
            # Throttle pypistats.org requests to avoid 429s
            await asyncio.sleep(0.4)
            dl_rows = await _fetch_pypi_downloads(client, tool_id, package, now)
            all_rows.extend(meta_rows)
            all_rows.extend(dl_rows)

            if (i + 1) % 50 == 0:
                logger.info("  PyPI: %d / %d packages", i + 1, len(valid))

    return all_rows
