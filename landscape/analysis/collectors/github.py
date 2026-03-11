"""GitHub GraphQL API collector for repository metrics."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx

from landscape.analysis.metrics import MetricRow

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"

# GraphQL query template for a single repo alias
_REPO_FRAGMENT = """
  {alias}: repository(owner: "{owner}", name: "{repo}") {{
    stargazerCount
    forkCount
    pushedAt
    isArchived
    licenseInfo {{ spdxId }}
    issues(states: OPEN) {{ totalCount }}
    releases(first: 1, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      nodes {{ publishedAt }}
    }}
    defaultBranchRef {{
      target {{
        ... on Commit {{ history {{ totalCount }} }}
      }}
    }}
  }}
"""

BATCH_SIZE = 30  # Repos per GraphQL query (keep under complexity limit)


def _build_query(repos: list[tuple[str, str, str]]) -> str:
    """Build a batched GraphQL query.

    Args:
        repos: List of (alias, owner, repo_name) tuples.
    """
    fragments = []
    for alias, owner, repo in repos:
        fragments.append(_REPO_FRAGMENT.format(alias=alias, owner=owner, repo=repo))
    return "query {\n" + "\n".join(fragments) + "\n}"


def _parse_repo_data(
    tool_id: int,
    data: dict,
    now: datetime,
) -> list[MetricRow]:
    """Parse GraphQL response for a single repo into MetricRows."""
    rows: list[MetricRow] = []

    if not data:
        return rows

    rows.append(
        MetricRow(tool_id, "github_stars", float(data["stargazerCount"]), "github_api", now, None)
    )
    rows.append(
        MetricRow(tool_id, "github_forks", float(data["forkCount"]), "github_api", now, None)
    )
    rows.append(
        MetricRow(
            tool_id,
            "github_open_issues",
            float(data["issues"]["totalCount"]),
            "github_api",
            now,
            None,
        )
    )

    # Days since last push
    pushed = data.get("pushedAt")
    if pushed:
        pushed_dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
        days_ago = (now - pushed_dt).days
        rows.append(
            MetricRow(
                tool_id, "github_last_push_days_ago", float(days_ago), "github_api", now, None
            )
        )

    # Days since last release
    releases = data.get("releases", {}).get("nodes", [])
    if releases and releases[0].get("publishedAt"):
        rel_dt = datetime.fromisoformat(releases[0]["publishedAt"].replace("Z", "+00:00"))
        days_ago = (now - rel_dt).days
        rows.append(
            MetricRow(
                tool_id, "github_last_release_days_ago", float(days_ago), "github_api", now, None
            )
        )

    # Total commits
    branch_ref = data.get("defaultBranchRef")
    if branch_ref and branch_ref.get("target"):
        history = branch_ref["target"].get("history", {})
        if history.get("totalCount"):
            rows.append(
                MetricRow(
                    tool_id,
                    "github_total_commits",
                    float(history["totalCount"]),
                    "github_api",
                    now,
                    None,
                )
            )

    # License + archived as metadata
    meta = {}
    if data.get("isArchived"):
        meta["archived"] = True
    license_info = data.get("licenseInfo")
    if license_info:
        meta["license_spdx"] = license_info.get("spdxId")
    if meta:
        rows.append(MetricRow(tool_id, "github_metadata", 0.0, "github_api", now, json.dumps(meta)))

    return rows


async def collect_github_metrics(
    tools: list[dict],
    *,
    token: str | None = None,
    now: datetime | None = None,
) -> list[MetricRow]:
    """Collect GitHub metrics for tools that have github_repo set.

    Args:
        tools: List of dicts with 'tool_id' and 'github_repo' keys.
        token: GitHub personal access token. Falls back to GITHUB_TOKEN env var.
        now: Timestamp for measurements (defaults to UTC now).
    """
    import os

    token = token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.error("No GitHub token available. Set GITHUB_TOKEN env var.")
        return []

    now = now or datetime.now(UTC)
    all_rows: list[MetricRow] = []

    # Filter to tools with github_repo
    valid = [(t["tool_id"], t["github_repo"]) for t in tools if t.get("github_repo")]
    if not valid:
        return []

    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        for batch_start in range(0, len(valid), BATCH_SIZE):
            batch = valid[batch_start : batch_start + BATCH_SIZE]

            # Build aliases and query
            repo_list: list[tuple[str, str, str]] = []
            alias_to_tool_id: dict[str, int] = {}
            for i, (tool_id, repo_slug) in enumerate(batch):
                parts = repo_slug.split("/")
                if len(parts) != 2:
                    logger.warning("Invalid repo slug: %s", repo_slug)
                    continue
                alias = f"r{i}"
                repo_list.append((alias, parts[0], parts[1]))
                alias_to_tool_id[alias] = tool_id

            if not repo_list:
                continue

            query = _build_query(repo_list)

            try:
                resp = await client.post(GRAPHQL_URL, json={"query": query})
                resp.raise_for_status()
                result = resp.json()
            except httpx.HTTPError as e:
                logger.error("GitHub GraphQL request failed: %s", e)
                continue

            if "errors" in result:
                for err in result["errors"]:
                    logger.warning("GraphQL error: %s", err.get("message", err))

            data = result.get("data", {})
            for alias, tool_id in alias_to_tool_id.items():
                repo_data = data.get(alias)
                if repo_data:
                    all_rows.extend(_parse_repo_data(tool_id, repo_data, now))

            logger.info(
                "  GitHub batch %d-%d: %d repos queried",
                batch_start + 1,
                batch_start + len(batch),
                len(repo_list),
            )

    return all_rows
