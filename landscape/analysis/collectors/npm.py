"""npm registry + download stats collector."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx

from landscape.analysis.metrics import MetricRow

logger = logging.getLogger(__name__)

NPM_REGISTRY_URL = "https://registry.npmjs.org/{package}"
NPM_DOWNLOADS_URL = "https://api.npmjs.org/downloads/point/last-week/{package}"


async def collect_npm_metrics(
    tools: list[dict],
    *,
    now: datetime | None = None,
) -> list[MetricRow]:
    """Collect npm metrics for tools that have npm_package set."""
    now = now or datetime.now(UTC)
    all_rows: list[MetricRow] = []

    valid = [(t["tool_id"], t["npm_package"]) for t in tools if t.get("npm_package")]
    if not valid:
        return []

    import asyncio

    async with httpx.AsyncClient(timeout=15.0) as client:
        for tool_id, package in valid:
            # Weekly downloads — throttle to avoid 429s
            await asyncio.sleep(0.4)
            try:
                resp = await client.get(
                    NPM_DOWNLOADS_URL.format(package=package),
                    follow_redirects=True,
                )
                if resp.status_code == 429:
                    logger.info("npm downloads 429 for %s, retrying after 2s", package)
                    await asyncio.sleep(2.0)
                    resp = await client.get(
                        NPM_DOWNLOADS_URL.format(package=package),
                        follow_redirects=True,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("downloads"):
                        all_rows.append(
                            MetricRow(
                                tool_id,
                                "npm_downloads_weekly",
                                float(data["downloads"]),
                                "npm_stats",
                                now,
                                None,
                            )
                        )
            except httpx.HTTPError as e:
                logger.warning("npm downloads fetch failed for %s: %s", package, e)

            # Package metadata (registry.npmjs.org doesn't rate-limit)
            try:
                resp = await client.get(
                    NPM_REGISTRY_URL.format(package=package),
                    headers={"Accept": "application/vnd.npm.install-v1+json"},
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    dist_tags = data.get("dist-tags", {})
                    meta = {"latest_version": dist_tags.get("latest")}
                    all_rows.append(
                        MetricRow(tool_id, "npm_metadata", 0.0, "npm_stats", now, json.dumps(meta))
                    )
            except httpx.HTTPError as e:
                logger.warning("npm registry fetch failed for %s: %s", package, e)

    return all_rows
