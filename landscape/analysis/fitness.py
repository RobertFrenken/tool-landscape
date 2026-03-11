"""Fitness scoring: score tools against project capabilities.

For each (tool, capability) pair, computes a fitness score 0-100 by combining:
- Quantitative metrics (downloads, stars, OpenSSF score, recency)
- Qualitative enums (ceiling tier, momentum, HPC compat, lock-in risk, docs, overhead)
- Requirement matching (tool properties vs capability ceiling_requirements)

Weights are configurable via DEFAULT_WEIGHTS dict.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import duckdb

# ── Ordinal enum scales (position → 0-1 normalized) ─────────────────────────

TIER_SCALE: dict[str | None, float] = {
    None: 0.0,
    "low": 0.25,
    "medium": 0.5,
    "high": 0.75,
    "extensive": 1.0,
}

MOMENTUM_SCALE: dict[str | None, float] = {
    None: 0.0,
    "declining": 0.0,
    "stable": 0.5,
    "growing": 1.0,
}

COST_SCALE: dict[str | None, float] = {
    None: 0.5,  # unknown = neutral
    "low": 1.0,
    "medium": 0.5,
    "high": 0.0,
}

DOC_SCALE: dict[str | None, float] = {
    None: 0.0,
    "poor": 0.0,
    "adequate": 0.5,
    "excellent": 1.0,
}

OVERHEAD_SCALE: dict[str | None, float] = {
    None: 0.5,
    "minimal": 1.0,
    "moderate": 0.5,
    "heavy": 0.0,
}

HPC_SCALE: dict[str | None, float] = {
    None: 0.0,
    "cloud_only": 0.0,
    "adaptable": 0.5,
    "native": 1.0,
}

MATURITY_SCALE: dict[str | None, float] = {
    None: 0.0,
    "archived": 0.0,
    "experimental": 0.25,
    "early": 0.5,
    "growth": 0.75,
    "production": 1.0,
}

# ── Default scoring weights ──────────────────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    # Qualitative signals (from tool enums)
    "ceiling_fit": 0.25,  # Does the tool's ceiling meet the capability's needs?
    "momentum": 0.15,  # Community health / trajectory
    "lock_in_risk": 0.10,  # Inverted: low lock-in = high score
    "hpc_compat": 0.10,  # HPC compatibility
    "docs": 0.05,  # Documentation quality
    "overhead": 0.05,  # Resource overhead (inverted)
    "maturity": 0.05,  # Production readiness
    # Quantitative metrics (from tool_metrics table)
    "downloads": 0.10,  # PyPI/npm monthly downloads (log-scaled)
    "openssf": 0.05,  # OpenSSF scorecard
    "stars": 0.05,  # GitHub stars (log-scaled)
    "recency": 0.05,  # Days since last release (inverted)
}


@dataclass
class ScoredTool:
    """Result of scoring a single tool against a capability."""

    tool_id: int
    tool_name: str
    capability_id: int
    capability_name: str
    floor_coverage: float  # 0-100
    ceiling_coverage: float  # 0-100
    overall_fitness: float  # 0-100
    components: dict[str, float] = field(default_factory=dict)  # component → 0-1
    reasoning: str = ""


def _log_normalize(value: float, floor: float = 0.0, ceiling: float = 1e9) -> float:
    """Normalize a value using log scale, clamped to [0, 1].

    Good for downloads/stars that span many orders of magnitude.
    """
    if value <= floor:
        return 0.0
    if ceiling <= floor:
        return 1.0
    log_val = math.log1p(value - floor)
    log_max = math.log1p(ceiling - floor)
    return min(1.0, log_val / log_max)


def _recency_score(days_since_release: float) -> float:
    """Score recency: 0 days = 1.0, 365 days = 0.5, 730+ days = 0.0."""
    if days_since_release <= 0:
        return 1.0
    if days_since_release >= 730:
        return 0.0
    return max(0.0, 1.0 - days_since_release / 730.0)


def _ceiling_meets_requirement(tool_ceiling: str | None, required_level: str | None) -> float:
    """Check if a tool's ceiling tier meets or exceeds the required level.

    Returns 1.0 if meets/exceeds, partial credit if close, 0.0 if far below.
    """
    tool_val = TIER_SCALE.get(tool_ceiling, 0.0)
    req_val = TIER_SCALE.get(required_level, 0.0)

    if req_val == 0.0:
        # No requirement specified, give full credit
        return 1.0
    if tool_val >= req_val:
        return 1.0
    # Partial credit: how close is the tool?
    return tool_val / req_val


def _check_bool_requirement(tool_value: bool, required: bool) -> float:
    """Check a boolean requirement. Returns 1.0 if met, 0.0 if not."""
    if not required:
        return 1.0
    return 1.0 if tool_value else 0.0


def _check_hpc_requirement(tool_hpc: str | None, required_values: list[str] | None) -> float:
    """Check if tool's HPC compatibility matches required values."""
    if not required_values:
        return 1.0
    if tool_hpc is None:
        return 0.0
    return 1.0 if tool_hpc in required_values else 0.0


def compute_requirement_coverage(tool: dict, requirements: dict) -> tuple[float, list[str]]:
    """Compute how well a tool's properties cover a capability's requirements.

    Returns (coverage 0-1, list of reasoning strings).
    """
    if not requirements:
        return 1.0, ["No specific requirements defined"]

    checks: list[tuple[str, float]] = []
    reasons: list[str] = []

    for key, req_val in requirements.items():
        if key == "offline_capable":
            score = _check_bool_requirement(tool.get("offline_capable", False), req_val)
            checks.append((key, score))
            if score < 1.0:
                reasons.append("offline_capable: required but not supported")

        elif key == "hpc_compatible":
            if isinstance(req_val, list):
                score = _check_hpc_requirement(tool.get("hpc_compatible"), req_val)
            else:
                score = 1.0 if tool.get("hpc_compatible") == req_val else 0.0
            checks.append((key, score))
            if score < 1.0:
                reasons.append(f"hpc_compatible: {tool.get('hpc_compatible')} not in {req_val}")

        elif key == "collaboration_model":
            if isinstance(req_val, list):
                collab = tool.get("collaboration_model")
                score = 1.0 if collab in req_val else 0.0
            else:
                score = 1.0 if tool.get("collaboration_model") == req_val else 0.0
            checks.append((key, score))
            if score < 1.0:
                reasons.append(
                    f"collaboration_model: {tool.get('collaboration_model')} not in {req_val}"
                )

        elif isinstance(req_val, bool):
            # Generic boolean requirement — we can't check arbitrary bools from
            # tool enum columns, so skip (give neutral score)
            pass

        # Skip non-checkable requirements (strings, lists of algorithms, etc.)
        # These would need domain-specific matching

    if not checks:
        return 0.5, ["No automatically checkable requirements"]

    coverage = sum(s for _, s in checks) / len(checks)
    return coverage, reasons


def get_latest_metrics(con: duckdb.DuckDBPyConnection, tool_id: int) -> dict[str, float]:
    """Get the most recent value for each metric name for a tool."""
    rows = con.execute(
        """
        SELECT metric_name, value
        FROM tool_metrics
        WHERE tool_id = $1
          AND (tool_id, metric_name, measured_at) IN (
              SELECT tool_id, metric_name, max(measured_at)
              FROM tool_metrics
              WHERE tool_id = $1
              GROUP BY tool_id, metric_name
          )
        """,
        [tool_id],
    ).fetchall()
    return {name: value for name, value in rows}


def score_tool_capability(
    tool: dict,
    capability: dict,
    metrics: dict[str, float],
    weights: dict[str, float] | None = None,
) -> ScoredTool:
    """Score a single tool against a single capability.

    Args:
        tool: Row from tools table as dict (column_name → value).
        capability: Row from capabilities table as dict.
        metrics: Latest metric values for this tool {metric_name: value}.
        weights: Override default weights. Keys must match DEFAULT_WEIGHTS.

    Returns:
        ScoredTool with scores and breakdown.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    components: dict[str, float] = {}
    reasons: list[str] = []

    # Parse ceiling_requirements from JSON
    ceiling_reqs = capability.get("ceiling_requirements", {})
    if isinstance(ceiling_reqs, str):
        ceiling_reqs = json.loads(ceiling_reqs)

    # ── Qualitative scoring ──────────────────────────────────────────────

    # Ceiling fit: does the tool's capability ceiling meet the need?
    # Use the capability's implicit ceiling level (derived from requirements complexity)
    components["ceiling_fit"] = TIER_SCALE.get(tool.get("capability_ceiling"), 0.0)

    # Momentum
    components["momentum"] = MOMENTUM_SCALE.get(tool.get("community_momentum"), 0.0)

    # Lock-in risk (inverted: low risk = high score)
    components["lock_in_risk"] = COST_SCALE.get(tool.get("lock_in_risk"), 0.5)

    # HPC compatibility
    components["hpc_compat"] = HPC_SCALE.get(tool.get("hpc_compatible"), 0.0)

    # Documentation quality
    components["docs"] = DOC_SCALE.get(tool.get("documentation_quality"), 0.0)

    # Resource overhead (inverted: minimal = high score)
    components["overhead"] = OVERHEAD_SCALE.get(tool.get("resource_overhead"), 0.5)

    # Maturity
    components["maturity"] = MATURITY_SCALE.get(tool.get("maturity"), 0.0)

    # ── Quantitative scoring (from metrics) ──────────────────────────────

    # Downloads (log-scaled, 100 → 0.0, 100M → 1.0)
    if "pypi_downloads_monthly" in metrics:
        components["downloads"] = _log_normalize(
            metrics["pypi_downloads_monthly"], floor=100, ceiling=1e8
        )
    elif "npm_downloads_monthly" in metrics:
        components["downloads"] = _log_normalize(
            metrics["npm_downloads_monthly"], floor=100, ceiling=1e8
        )

    # OpenSSF score (0-10 scale)
    if "openssf_score" in metrics:
        components["openssf"] = min(1.0, metrics["openssf_score"] / 10.0)

    # GitHub stars (log-scaled, 10 → 0.0, 200K → 1.0)
    if "github_stars" in metrics:
        components["stars"] = _log_normalize(metrics["github_stars"], floor=10, ceiling=200_000)

    # Recency (days since last release)
    if "days_since_last_release" in metrics:
        components["recency"] = _recency_score(metrics["days_since_last_release"])

    # ── Requirement coverage (floor/ceiling) ─────────────────────────────

    floor_reqs = capability.get("floor_requirements", {})
    if isinstance(floor_reqs, str):
        floor_reqs = json.loads(floor_reqs)

    floor_coverage, floor_reasons = compute_requirement_coverage(tool, floor_reqs)
    ceiling_coverage, ceiling_reasons = compute_requirement_coverage(tool, ceiling_reqs)
    reasons.extend(ceiling_reasons)

    # ── Weighted combination ─────────────────────────────────────────────

    total_weight = 0.0
    weighted_sum = 0.0

    for component_name, component_score in components.items():
        component_weight = w.get(component_name, 0.0)
        if component_weight > 0:
            weighted_sum += component_score * component_weight
            total_weight += component_weight

    # Normalize by actual weight used (handles missing metrics gracefully)
    if total_weight > 0:
        qualitative_score = weighted_sum / total_weight
    else:
        qualitative_score = 0.0

    # Overall fitness blends qualitative score with ceiling coverage
    # 60% qualitative enum/metric score, 40% requirement coverage
    overall = qualitative_score * 0.6 + ceiling_coverage * 0.4

    # Build reasoning string
    top_components = sorted(components.items(), key=lambda x: x[1], reverse=True)[:3]
    top_str = ", ".join(f"{k}={v:.2f}" for k, v in top_components)
    reason_parts = [f"top signals: {top_str}"]
    if reasons:
        reason_parts.extend(reasons[:3])

    return ScoredTool(
        tool_id=tool["tool_id"],
        tool_name=tool["name"],
        capability_id=capability["capability_id"],
        capability_name=capability["name"],
        floor_coverage=round(floor_coverage * 100, 1),
        ceiling_coverage=round(ceiling_coverage * 100, 1),
        overall_fitness=round(overall * 100, 1),
        components=components,
        reasoning="; ".join(reason_parts),
    )


def score_project(
    con: duckdb.DuckDBPyConnection,
    project_name: str,
    weights: dict[str, float] | None = None,
    top_n: int = 10,
) -> dict[str, list[ScoredTool]]:
    """Score all tools against all capabilities for a project.

    Returns:
        Dict mapping capability_name → list of ScoredTool (sorted by overall_fitness desc).
    """
    # Get project
    project = con.execute(
        "SELECT project_id FROM projects WHERE lower(name) = lower($1)",
        [project_name],
    ).fetchone()
    if not project:
        raise ValueError(f"Project '{project_name}' not found")
    project_id = project[0]

    # Get capabilities
    cap_rows = con.execute(
        "SELECT * FROM capabilities WHERE project_id = $1",
        [project_id],
    ).fetchall()
    cap_cols = [desc[0] for desc in con.description]
    capabilities = [dict(zip(cap_cols, row)) for row in cap_rows]

    # Get all tools
    tool_rows = con.execute("SELECT * FROM tools").fetchall()
    tool_cols = [desc[0] for desc in con.description]
    tools = [dict(zip(tool_cols, row)) for row in tool_rows]

    results: dict[str, list[ScoredTool]] = {}

    for cap in capabilities:
        scored: list[ScoredTool] = []
        for tool in tools:
            metrics = get_latest_metrics(con, tool["tool_id"])
            st = score_tool_capability(tool, cap, metrics, weights)
            scored.append(st)
        scored.sort(key=lambda s: s.overall_fitness, reverse=True)
        results[cap["name"]] = scored[:top_n]

    return results


def score_single_tool(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    weights: dict[str, float] | None = None,
) -> list[ScoredTool]:
    """Score a single tool against all capabilities.

    Returns:
        List of ScoredTool (one per capability), sorted by overall_fitness desc.
    """
    tool_row = con.execute(
        "SELECT * FROM tools WHERE lower(name) = lower($1)", [tool_name]
    ).fetchone()
    if not tool_row:
        raise ValueError(f"Tool '{tool_name}' not found")
    tool_cols = [desc[0] for desc in con.description]
    tool = dict(zip(tool_cols, tool_row))

    metrics = get_latest_metrics(con, tool["tool_id"])

    cap_rows = con.execute("SELECT * FROM capabilities").fetchall()
    cap_cols = [desc[0] for desc in con.description]
    capabilities = [dict(zip(cap_cols, row)) for row in cap_rows]

    results: list[ScoredTool] = []
    for cap in capabilities:
        st = score_tool_capability(tool, cap, metrics, weights)
        results.append(st)

    results.sort(key=lambda s: s.overall_fitness, reverse=True)
    return results


def persist_scores(
    con: duckdb.DuckDBPyConnection,
    scores: list[ScoredTool],
    method: str = "algorithm_v1",
) -> int:
    """Write scored results to the fitness table.

    Uses INSERT OR REPLACE semantics on (tool_id, capability_id).
    Returns number of rows written.
    """
    count = 0
    for s in scores:
        con.execute(
            """
            DELETE FROM fitness
            WHERE tool_id = $1 AND capability_id = $2
            """,
            [s.tool_id, s.capability_id],
        )
        con.execute(
            """
            INSERT INTO fitness (
                tool_id, capability_id,
                floor_coverage, ceiling_coverage, overall_fitness,
                method, reasoning
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [
                s.tool_id,
                s.capability_id,
                s.floor_coverage,
                s.ceiling_coverage,
                s.overall_fitness,
                method,
                s.reasoning,
            ],
        )
        count += 1
    return count
