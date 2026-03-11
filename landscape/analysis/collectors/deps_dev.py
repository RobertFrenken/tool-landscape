"""deps.dev API collector for OpenSSF Scorecard, advisories, and dependency edges.

Uses the deps.dev v3alpha batch endpoints:
- GetProjectBatch: OpenSSF Scorecard scores for GitHub projects
- GetVersionBatch: Advisory counts for PyPI/npm packages
- GetDependencies: Dependency graphs (individual calls, derives 'requires' edges)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import duckdb
import httpx

from landscape.analysis.metrics import MetricRow

logger = logging.getLogger(__name__)

DEPS_DEV_BASE = "https://api.deps.dev/v3alpha"
PROJECT_BATCH_URL = f"{DEPS_DEV_BASE}/projectbatch"
VERSION_BATCH_URL = f"{DEPS_DEV_BASE}/versionbatch"
DEPS_URL_TEMPLATE = (
    f"{DEPS_DEV_BASE}/systems/{{system}}/packages/{{name}}/versions/{{version}}:dependencies"
)

BATCH_SIZE = 500  # Well under 5000 limit; keeps responses manageable


async def _fetch_scorecard_batch(
    client: httpx.AsyncClient,
    repos: list[tuple[int, str]],
    now: datetime,
) -> list[MetricRow]:
    """Fetch OpenSSF Scorecard scores via GetProjectBatch.

    Args:
        repos: List of (tool_id, "owner/repo") tuples.
    """
    rows: list[MetricRow] = []

    for batch_start in range(0, len(repos), BATCH_SIZE):
        batch = repos[batch_start : batch_start + BATCH_SIZE]
        requests = [{"projectKey": {"id": f"github.com/{repo}"}} for _, repo in batch]

        try:
            resp = await client.post(
                PROJECT_BATCH_URL,
                json={"requests": requests},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error(
                "deps.dev project batch failed (batch %d): %s",
                batch_start,
                e,
            )
            continue

        # Map project IDs back to tool_ids
        id_to_tool = {f"github.com/{repo}": tool_id for tool_id, repo in batch}

        for item in data.get("responses", []):
            project = item.get("project")
            if not project:
                continue

            project_id = project.get("projectKey", {}).get("id", "")
            tool_id = id_to_tool.get(project_id)
            if tool_id is None:
                continue

            scorecard = project.get("scorecard")
            if scorecard and scorecard.get("overallScore") is not None:
                score = float(scorecard["overallScore"])
                # Store individual check scores in metadata
                checks = {}
                for check in scorecard.get("checks", []):
                    if check.get("name") and check.get("score") is not None:
                        checks[check["name"]] = check["score"]

                meta = {"checks": checks} if checks else None
                rows.append(
                    MetricRow(
                        tool_id,
                        "openssf_score",
                        score,
                        "deps_dev",
                        now,
                        json.dumps(meta) if meta else None,
                    )
                )

            # Stars/forks from deps.dev (as cross-reference)
            stars = project.get("starsCount")
            if stars is not None and stars > 0:
                rows.append(
                    MetricRow(
                        tool_id,
                        "depsdev_stars",
                        float(stars),
                        "deps_dev",
                        now,
                        None,
                    )
                )

        logger.info(
            "  deps.dev scorecard batch %d-%d: %d projects",
            batch_start + 1,
            batch_start + len(batch),
            len(batch),
        )

    return rows


async def _fetch_advisories_batch(
    client: httpx.AsyncClient,
    packages: list[tuple[int, str, str, str]],
    now: datetime,
) -> list[MetricRow]:
    """Fetch advisory counts via GetVersionBatch.

    Args:
        packages: List of (tool_id, system, name, version) tuples.
    """
    rows: list[MetricRow] = []
    if not packages:
        return rows

    for batch_start in range(0, len(packages), BATCH_SIZE):
        batch = packages[batch_start : batch_start + BATCH_SIZE]
        requests = [
            {
                "versionKey": {
                    "system": system,
                    "name": name,
                    "version": version,
                }
            }
            for _, system, name, version in batch
        ]

        try:
            resp = await client.post(
                VERSION_BATCH_URL,
                json={"requests": requests},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.error(
                "deps.dev version batch failed (batch %d): %s",
                batch_start,
                e,
            )
            continue

        # Build lookup: (system, name, version) -> tool_id
        key_to_tool = {(system, name, version): tool_id for tool_id, system, name, version in batch}

        for item in data.get("responses", []):
            version_data = item.get("version")
            if not version_data:
                continue

            vk = version_data.get("versionKey", {})
            key = (
                vk.get("system", ""),
                vk.get("name", ""),
                vk.get("version", ""),
            )
            tool_id = key_to_tool.get(key)
            if tool_id is None:
                continue

            advisories = version_data.get("advisoryKeys", [])
            if advisories:
                advisory_ids = [a.get("id", "") for a in advisories]
                rows.append(
                    MetricRow(
                        tool_id,
                        "known_vulnerabilities",
                        float(len(advisories)),
                        "deps_dev",
                        now,
                        json.dumps({"advisory_ids": advisory_ids}),
                    )
                )

    return rows


async def _derive_dependency_edges(
    client: httpx.AsyncClient,
    packages: list[tuple[int, str, str, str]],
    tool_packages: dict[str, int],
    con: duckdb.DuckDBPyConnection,
) -> int:
    """Fetch dependencies and insert 'requires' edges for known tools.

    Args:
        packages: List of (tool_id, system, name, version) tuples.
        tool_packages: Map of lowercase "system/name" -> tool_id for lookup.
        con: Database connection for edge insertion.

    Returns:
        Number of new edges inserted.
    """
    edge_count = 0

    for tool_id, system, name, version in packages:
        url = DEPS_URL_TEMPLATE.format(system=system, name=name, version=version)
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except httpx.HTTPError:
            continue

        for node in data.get("nodes", []):
            vk = node.get("versionKey", {})
            dep_system = vk.get("system", "")
            dep_name = vk.get("name", "")
            dep_key = f"{dep_system}/{dep_name}".lower()

            target_id = tool_packages.get(dep_key)
            if target_id and target_id != tool_id:
                try:
                    con.execute(
                        """
                        INSERT INTO edges
                            (source_id, target_id, relation,
                             source_info, evidence)
                        VALUES ($1, $2, 'requires', 'deps_dev',
                            'Derived from deps.dev dependency graph')
                        """,
                        [tool_id, target_id],
                    )
                    edge_count += 1
                except Exception:
                    pass  # Duplicate edge

    return edge_count


def _build_tool_package_map(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, int]:
    """Build a map of lowercase "SYSTEM/package_name" -> tool_id."""
    mapping: dict[str, int] = {}

    rows = con.execute("SELECT tool_id, pypi_package, npm_package FROM tools").fetchall()

    for tool_id, pypi, npm in rows:
        if pypi:
            mapping[f"pypi/{pypi}".lower()] = tool_id
        if npm:
            mapping[f"npm/{npm}".lower()] = tool_id

    return mapping


def _get_package_versions(
    con: duckdb.DuckDBPyConnection,
    tool_names: list[str] | None = None,
) -> list[tuple[int, str, str, str]]:
    """Get (tool_id, system, name, version) from stored metrics metadata.

    Extracts latest_version from pypi_metadata or npm_metadata metrics.
    """
    packages: list[tuple[int, str, str, str]] = []

    conditions = []
    params: list = []
    idx = 1

    if tool_names:
        placeholders = ", ".join(f"${i}" for i in range(idx, idx + len(tool_names)))
        conditions.append(f"t.name IN ({placeholders})")
        params.extend(tool_names)

    where = f" AND {' AND '.join(conditions)}" if conditions else ""

    # PyPI packages with known versions
    rows = con.execute(
        f"""
        SELECT t.tool_id, t.pypi_package, m.metadata
        FROM tools t
        JOIN tool_metrics m ON t.tool_id = m.tool_id
        WHERE t.pypi_package IS NOT NULL
            AND m.metric_name = 'pypi_metadata'
            AND m.metadata IS NOT NULL
            {where}
        """,  # noqa: S608
        params,
    ).fetchall()

    for tool_id, package, meta_str in rows:
        try:
            meta = json.loads(str(meta_str))
            version = meta.get("latest_version") or meta.get("version")
            if version:
                packages.append((tool_id, "PYPI", package, version))
        except (json.JSONDecodeError, TypeError):
            pass

    # npm packages with known versions
    rows = con.execute(
        f"""
        SELECT t.tool_id, t.npm_package, m.metadata
        FROM tools t
        JOIN tool_metrics m ON t.tool_id = m.tool_id
        WHERE t.npm_package IS NOT NULL
            AND m.metric_name = 'npm_metadata'
            AND m.metadata IS NOT NULL
            {where}
        """,  # noqa: S608
        params,
    ).fetchall()

    for tool_id, package, meta_str in rows:
        try:
            meta = json.loads(str(meta_str))
            version = meta.get("latest_version")
            if version:
                packages.append((tool_id, "NPM", package, version))
        except (json.JSONDecodeError, TypeError):
            pass

    return packages


async def collect_depsdev_metrics(
    tools: list[dict],
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
    tool_names: list[str] | None = None,
    derive_edges: bool = True,
) -> list[MetricRow]:
    """Collect deps.dev metrics and optionally derive dependency edges.

    Args:
        tools: List of dicts with tool_id, github_repo, etc.
        con: DB connection (needed for edge insertion and version lookup).
        now: Timestamp for measurements.
        tool_names: Optional filter for specific tools.
        derive_edges: Whether to derive 'requires' edges from dependencies.

    Returns:
        List of MetricRows for scorecard and advisory metrics.
    """
    now = now or datetime.now(UTC)
    all_rows: list[MetricRow] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. OpenSSF Scorecard via project batch
        repos = [(t["tool_id"], t["github_repo"]) for t in tools if t.get("github_repo")]
        if repos:
            logger.info("Fetching OpenSSF Scorecard for %d projects", len(repos))
            scorecard_rows = await _fetch_scorecard_batch(client, repos, now)
            all_rows.extend(scorecard_rows)
            logger.info("  Got %d scorecard metrics", len(scorecard_rows))

        # 2. Advisories via version batch (needs prior pypi/npm metrics)
        packages = _get_package_versions(con, tool_names)
        if packages:
            logger.info(
                "Fetching advisories for %d package versions",
                len(packages),
            )
            advisory_rows = await _fetch_advisories_batch(client, packages, now)
            all_rows.extend(advisory_rows)
            logger.info("  Got %d advisory metrics", len(advisory_rows))

        # 3. Derive dependency edges
        if derive_edges and packages:
            tool_packages = _build_tool_package_map(con)
            logger.info(
                "Deriving dependency edges for %d packages (%d known tool packages)",
                len(packages),
                len(tool_packages),
            )
            edge_count = await _derive_dependency_edges(client, packages, tool_packages, con)
            logger.info("  Derived %d new 'requires' edges", edge_count)

    return all_rows
