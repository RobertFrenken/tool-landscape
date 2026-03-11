"""Shopping/matching engine: two-phase pipeline (filter + score) for tool selection.

Given a ProjectSpec, filters tools by hard constraints via DuckDB SQL, then scores
survivors by fitness, weighted preferences, and stack coherence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import duckdb

from landscape.analysis.fitness import get_latest_metrics, score_tool_capability
from landscape.models.spec import (
    ARRAY_FIELDS,
    BOOLEAN_FIELDS,
    METRIC_FIELDS,
    ORDINAL_HIGHER_BETTER,
    ORDINAL_LOWER_BETTER,
    VALID_ENUMS,
    ComponentSpec,
    ProjectSpec,
    WeightedPreference,
    parse_constraint_values,
)

# Edge types that signal coherence between tools
COHERENCE_EDGE_TYPES = {"integrates_with", "often_paired", "feeds_into"}

# Default weight for neighborhood co-membership (no direct edge)
NEIGHBORHOOD_COHERENCE = 0.3


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class ScoredCandidate:
    """A tool that survived filtering, with scored breakdown."""

    tool_name: str
    tool_id: int
    combined_score: float  # 0-100
    fitness_score: float  # 0-100
    preference_score: float  # 0-100
    coherence_score: float  # 0-100
    is_current: bool = False  # True if this is the component's current_tool


@dataclass
class MatchReport:
    """Result of the filter+score pipeline for one component."""

    component_name: str
    total_tools: int
    filter_funnel: list[tuple[str, int]] = field(default_factory=list)
    scored_tools: list[ScoredCandidate] = field(default_factory=list)
    unmatched_notes: list[str] = field(default_factory=list)
    coherence_hits: int = 0


# ── Phase 1: Filter (Hard Constraints -> DuckDB SQL) ───────────────────────


def build_filter_query(component: ComponentSpec) -> tuple[str, list, list[str]]:
    """Build a DuckDB query that filters tools by hard constraints.

    Returns (sql_string, parameter_list, funnel_label_list).
    """
    predicates: list[str] = []
    params: list = []
    funnel_labels: list[str] = []
    param_idx = 0

    known = component.require.get_known_fields()

    for field_name, value in known.items():
        # ── Boolean fields ──────────────────────────────────────────
        if field_name in BOOLEAN_FIELDS:
            param_idx += 1
            predicates.append(f"{field_name} = ${param_idx}")
            params.append(value)
            funnel_labels.append(f"{field_name} = {value}")

        # ── Enum fields ─────────────────────────────────────────────
        elif field_name in VALID_ENUMS:
            includes, excludes = parse_constraint_values(value)
            if includes:
                placeholders = []
                for v in includes:
                    param_idx += 1
                    placeholders.append(f"${param_idx}")
                    params.append(v)
                in_clause = ", ".join(placeholders)
                predicates.append(
                    f"({field_name} IS NOT NULL AND CAST({field_name} AS VARCHAR) IN ({in_clause}))"
                )
                funnel_labels.append(f"{field_name} IN ({', '.join(includes)})")
            if excludes:
                not_placeholders = []
                for v in excludes:
                    param_idx += 1
                    not_placeholders.append(f"${param_idx}")
                    params.append(v)
                not_clause = ", ".join(not_placeholders)
                predicates.append(
                    f"({field_name} IS NULL OR CAST({field_name} AS VARCHAR) NOT IN ({not_clause}))"
                )
                funnel_labels.append(f"{field_name} NOT IN ({', '.join(excludes)})")

        # ── Array fields ────────────────────────────────────────────
        elif field_name in ARRAY_FIELDS:
            includes, excludes = parse_constraint_values(value)
            if includes:
                or_parts = []
                for v in includes:
                    param_idx += 1
                    or_parts.append(f"list_contains({field_name}, ${param_idx})")
                    params.append(v)
                predicates.append(f"({field_name} IS NOT NULL AND ({' OR '.join(or_parts)}))")
                funnel_labels.append(f"{field_name} contains any of ({', '.join(includes)})")
            if excludes:
                and_parts = []
                for v in excludes:
                    param_idx += 1
                    and_parts.append(f"NOT list_contains(COALESCE({field_name}, []), ${param_idx})")
                    params.append(v)
                predicates.append(f"({' AND '.join(and_parts)})")
                funnel_labels.append(f"{field_name} excludes ({', '.join(excludes)})")

        # ── Metric thresholds ───────────────────────────────────────
        elif field_name in METRIC_FIELDS:
            metric_name = METRIC_FIELDS[field_name]

            if field_name.startswith("max_"):
                op = "<="
            else:
                op = ">="

            # Parameterize both metric_name and threshold value
            param_idx += 1
            name_param = param_idx
            params.append(metric_name)

            param_idx += 1
            value_param = param_idx
            params.append(value)

            predicates.append(
                f"t.tool_id IN ("
                f"  SELECT tm.tool_id FROM tool_metrics tm"
                f"  WHERE tm.metric_name = ${name_param}"
                f"  GROUP BY tm.tool_id"
                f"  HAVING MAX(tm.value) {op} ${value_param}"
                f")"
            )
            funnel_labels.append(f"{metric_name} {op} {value}")

    # Build final query
    where = " AND ".join(predicates) if predicates else "1=1"
    sql = f"SELECT t.* FROM tools t WHERE {where}"

    return sql, params, funnel_labels


def _run_filter_funnel(
    con: duckdb.DuckDBPyConnection, component: ComponentSpec
) -> tuple[list[dict], list[tuple[str, int]]]:
    """Run the filter query and build the funnel trace.

    Returns (list of tool dicts, funnel steps).
    """
    total = con.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    funnel: list[tuple[str, int]] = [("all tools", total)]

    sql, params, labels = build_filter_query(component)

    # Execute combined filter
    rows = con.execute(sql, params).fetchall()
    cols = [desc[0] for desc in con.description]
    candidates = [dict(zip(cols, row)) for row in rows]

    if labels:
        funnel.append(("; ".join(labels), len(candidates)))

    return candidates, funnel


# ── Phase 2: Score ──────────────────────────────────────────────────────────


def _compute_preference_score(tool: dict, preferences: dict[str, WeightedPreference]) -> float:
    """Score a tool against weighted preferences. Returns 0-1."""
    if not preferences:
        return 0.5  # neutral when no preferences specified

    weighted_sum = 0.0
    multiplier_sum = 0.0

    for field_name, pref in preferences.items():
        match_score = _score_single_preference(tool, field_name, pref)
        if match_score is None:
            continue  # field not present on tool, skip

        mult = pref.multiplier
        weighted_sum += match_score * mult
        multiplier_sum += mult

    if multiplier_sum == 0.0:
        return 0.5
    return weighted_sum / multiplier_sum


def _score_single_preference(tool: dict, field_name: str, pref: WeightedPreference) -> float | None:
    """Score one preference field. Returns 0-1 or None if not scoreable."""
    tool_value = tool.get(field_name)

    # ── Ordinal higher-is-better ────────────────────────────────────
    if field_name in ORDINAL_HIGHER_BETTER:
        scale = ORDINAL_HIGHER_BETTER[field_name]
        return _ordinal_score(tool_value, pref.value, scale, higher_better=True)

    # ── Ordinal lower-is-better ─────────────────────────────────────
    if field_name in ORDINAL_LOWER_BETTER:
        scale = ORDINAL_LOWER_BETTER[field_name]
        return _ordinal_score(tool_value, pref.value, scale, higher_better=False)

    # ── Boolean ─────────────────────────────────────────────────────
    if field_name in BOOLEAN_FIELDS:
        if tool_value is None:
            return 0.0
        return 1.0 if bool(tool_value) == bool(pref.value) else 0.0

    # ── Array overlap ───────────────────────────────────────────────
    if field_name in ARRAY_FIELDS:
        tool_arr = tool_value if isinstance(tool_value, (list, tuple)) else []
        pref_vals = pref.value if isinstance(pref.value, (list, tuple)) else [pref.value]
        if not pref_vals:
            return 1.0
        overlap = len(set(tool_arr) & set(pref_vals))
        return overlap / len(pref_vals)

    # ── Enum exact match (non-ordinal) ──────────────────────────────
    if field_name in VALID_ENUMS:
        if tool_value is None:
            return 0.0
        tool_str = str(tool_value)
        if isinstance(pref.value, list):
            return 1.0 if tool_str in pref.value else 0.0
        return 1.0 if tool_str == str(pref.value) else 0.0

    return None  # unknown field


def _ordinal_score(
    tool_value: str | None,
    pref_value: str | list[str],
    scale: list[str],
    *,
    higher_better: bool,
) -> float:
    """Score an ordinal field. Returns 0-1."""
    if tool_value is None:
        return 0.0

    tool_str = str(tool_value)
    if tool_str not in scale:
        return 0.0

    # Preferred value: take the highest (or lowest) if a list
    if isinstance(pref_value, list):
        target = pref_value[0]
        for v in pref_value:
            if v in scale:
                target = v
    else:
        target = str(pref_value)

    if target not in scale:
        return 0.5  # unknown target, give neutral

    tool_pos = scale.index(tool_str)
    pref_pos = scale.index(target)

    if higher_better:
        # tool position / preferred position, capped at 1.0
        if pref_pos == 0:
            return 1.0
        return min(1.0, tool_pos / pref_pos)
    else:
        # Lower is better: invert positions. If tool is at or below preferred, full score.
        if tool_pos <= pref_pos:
            return 1.0
        # Penalty for being higher (worse) than preferred
        remaining = len(scale) - 1 - pref_pos
        if remaining == 0:
            return 0.0
        excess = tool_pos - pref_pos
        return max(0.0, 1.0 - excess / remaining)


def _compute_coherence_score(
    con: duckdb.DuckDBPyConnection, tool_id: int, stack_pins: list[str]
) -> float:
    """Score coherence with pinned stack tools. Returns 0-1."""
    if not stack_pins:
        return 0.0

    # Resolve pinned tool names to IDs
    placeholders = ", ".join(["?"] * len(stack_pins))
    pin_rows = con.execute(
        f"SELECT tool_id, name FROM tools WHERE lower(name) IN ({placeholders})",
        [p.lower() for p in stack_pins],
    ).fetchall()
    pin_ids = {row[0]: row[1] for row in pin_rows}

    if not pin_ids:
        return 0.0

    total_score = 0.0

    for pin_id in pin_ids:
        # Check for direct edge (either direction)
        edge = con.execute(
            """
            SELECT relation, weight FROM edges
            WHERE (source_id = ? AND target_id = ?)
               OR (source_id = ? AND target_id = ?)
            """,
            [tool_id, pin_id, pin_id, tool_id],
        ).fetchall()

        has_coherent_edge = False
        for relation, weight in edge:
            rel_str = str(relation)
            if rel_str in COHERENCE_EDGE_TYPES:
                # Scale edge weight to 0-1 (weights are typically 0.3-3.0)
                total_score += min(1.0, (weight or 1.0) / 3.0)
                has_coherent_edge = True
                break

        if not has_coherent_edge:
            # Check for shared neighborhood
            shared = con.execute(
                """
                SELECT COUNT(*) FROM neighborhood_members nm1
                JOIN neighborhood_members nm2
                  ON nm1.neighborhood_id = nm2.neighborhood_id
                WHERE nm1.tool_id = ? AND nm2.tool_id = ?
                """,
                [tool_id, pin_id],
            ).fetchone()[0]
            if shared > 0:
                total_score += NEIGHBORHOOD_COHERENCE

    # Normalize to 0-1
    max_possible = len(pin_ids)
    if max_possible == 0:
        return 0.0
    return min(1.0, total_score / max_possible)


def _build_synthetic_capability(component: ComponentSpec) -> dict:
    """Build a synthetic capability dict from a ComponentSpec for fitness scoring."""
    ceiling_reqs: dict = {}
    known = component.require.get_known_fields()

    for field_name, value in known.items():
        if field_name in BOOLEAN_FIELDS:
            ceiling_reqs[field_name] = value
        elif field_name in VALID_ENUMS and isinstance(value, list):
            includes, _ = parse_constraint_values(value)
            if includes:
                ceiling_reqs[field_name] = includes
        elif field_name == "hpc_compatible" and isinstance(value, list):
            includes, _ = parse_constraint_values(value)
            if includes:
                ceiling_reqs["hpc_compatible"] = includes

    return {
        "capability_id": -1,
        "name": component.description or "synthetic",
        "floor_requirements": "{}",
        "ceiling_requirements": ceiling_reqs,
    }


def score_candidates(
    candidates: list[dict],
    component: ComponentSpec,
    con: duckdb.DuckDBPyConnection,
    stack_pins: list[str],
) -> list[ScoredCandidate]:
    """Score candidate tools that survived Phase 1 filtering.

    Combined score = fitness(0.40) + preference(0.40) + coherence(0.20).
    """
    preferences = component.get_preferences()
    synthetic_cap = _build_synthetic_capability(component)
    current_name = (component.current_tool or "").lower()

    scored: list[ScoredCandidate] = []

    for tool in candidates:
        tool_id = tool["tool_id"]
        tool_name = tool["name"]

        # ── Fitness score (reuse existing scorer) ───────────────────
        metrics = get_latest_metrics(con, tool_id)
        fitness_result = score_tool_capability(tool, synthetic_cap, metrics)
        fitness_norm = fitness_result.overall_fitness  # already 0-100

        # ── Preference score ────────────────────────────────────────
        pref_raw = _compute_preference_score(tool, preferences)
        pref_norm = pref_raw * 100.0

        # ── Coherence score ─────────────────────────────────────────
        coherence_raw = _compute_coherence_score(con, tool_id, stack_pins)
        coherence_norm = coherence_raw * 100.0

        # ── Combined ────────────────────────────────────────────────
        combined = fitness_norm * 0.40 + pref_norm * 0.40 + coherence_norm * 0.20

        scored.append(
            ScoredCandidate(
                tool_name=tool_name,
                tool_id=tool_id,
                combined_score=round(combined, 1),
                fitness_score=round(fitness_norm, 1),
                preference_score=round(pref_norm, 1),
                coherence_score=round(coherence_norm, 1),
                is_current=tool_name.lower() == current_name,
            )
        )

    scored.sort(key=lambda s: s.combined_score, reverse=True)
    return scored


# ── Top-Level Entry Point ──────────────────────────────────────────────────


def shop(
    con: duckdb.DuckDBPyConnection,
    spec: ProjectSpec,
    component_name: str | None = None,
    top_n: int = 10,
) -> dict[str, MatchReport]:
    """Run the filter+score pipeline for each component in the spec.

    Args:
        con: DuckDB connection.
        spec: Parsed ProjectSpec with components and stack_pins.
        component_name: If given, only process this component.
        top_n: Maximum scored tools to keep per component.

    Returns:
        Dict of component_name -> MatchReport.
    """
    components = spec.components
    if component_name:
        if component_name not in components:
            raise ValueError(
                f"Component '{component_name}' not found in spec. "
                f"Available: {list(components.keys())}"
            )
        components = {component_name: components[component_name]}

    total_tools = con.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    reports: dict[str, MatchReport] = {}

    for comp_name, comp in components.items():
        # Phase 1: Filter
        candidates, funnel = _run_filter_funnel(con, comp)

        # Phase 2: Score
        scored = score_candidates(candidates, comp, con, spec.stack_pins)
        top_scored = scored[:top_n]

        # Coherence hits: count tools with non-zero coherence
        coherence_hits = sum(1 for s in top_scored if s.coherence_score > 0)

        # Unmatched notes: extra require fields that couldn't be SQL-filtered
        unmatched = []
        for field_name in comp.require.get_extra_fields():
            unmatched.append(f"require.{field_name}: not a known DB column (skipped)")
        for note in comp.notes:
            unmatched.append(f"note: {note}")

        reports[comp_name] = MatchReport(
            component_name=comp_name,
            total_tools=total_tools,
            filter_funnel=funnel,
            scored_tools=top_scored,
            unmatched_notes=unmatched,
            coherence_hits=coherence_hits,
        )

    return reports


# ── Pretty Printer ──────────────────────────────────────────────────────────


def reports_to_json(reports: dict[str, MatchReport]) -> str:
    """Serialize match reports to JSON string."""
    data = {}
    for comp_name, report in reports.items():
        data[comp_name] = {
            "component_name": report.component_name,
            "total_tools": report.total_tools,
            "filter_funnel": [{"label": l, "count": c} for l, c in report.filter_funnel],
            "scored_tools": [
                {
                    "rank": i + 1,
                    "tool_name": s.tool_name,
                    "tool_id": s.tool_id,
                    "combined_score": s.combined_score,
                    "fitness_score": s.fitness_score,
                    "preference_score": s.preference_score,
                    "coherence_score": s.coherence_score,
                    "is_current": s.is_current,
                }
                for i, s in enumerate(report.scored_tools)
            ],
            "unmatched_notes": report.unmatched_notes,
            "coherence_hits": report.coherence_hits,
        }
    return json.dumps(data, indent=2)


def persist_shop_results(
    con: duckdb.DuckDBPyConnection,
    reports: dict[str, MatchReport],
    project_name: str,
) -> int:
    """Write shop scores to the fitness table.

    Creates synthetic capability entries if needed, then inserts fitness scores.
    Returns the number of rows written.
    """
    # Resolve project_id
    row = con.execute(
        "SELECT project_id FROM projects WHERE lower(name) = lower(?)", [project_name]
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_name}' not found in database")
    project_id = row[0]

    rows_written = 0
    for comp_name, report in reports.items():
        # Find or create capability
        cap_row = con.execute(
            "SELECT capability_id FROM capabilities WHERE project_id = ? AND name = ?",
            [project_id, comp_name],
        ).fetchone()

        if cap_row:
            cap_id = cap_row[0]
        else:
            # Create a synthetic capability for this component
            con.execute(
                "INSERT INTO capabilities (project_id, name, description) VALUES (?, ?, ?)",
                [project_id, comp_name, "Auto-created by shop --persist"],
            )
            cap_id = con.execute(
                "SELECT capability_id FROM capabilities WHERE project_id = ? AND name = ?",
                [project_id, comp_name],
            ).fetchone()[0]

        # Insert fitness scores for each scored tool
        for sc in report.scored_tools:
            con.execute(
                """INSERT INTO fitness (tool_id, capability_id, floor_coverage,
                    ceiling_coverage, overall_fitness, method, reasoning)
                VALUES (?, ?, ?, ?, ?, 'shop', ?)""",
                [
                    sc.tool_id,
                    cap_id,
                    sc.fitness_score / 100.0,
                    sc.preference_score / 100.0,
                    sc.combined_score / 100.0,
                    f"fit={sc.fitness_score} pref={sc.preference_score} coher={sc.coherence_score}",
                ],
            )
            rows_written += 1

    return rows_written


def print_shop_report(reports: dict[str, MatchReport]) -> None:
    """Pretty-print match reports to stdout."""
    for comp_name, report in reports.items():
        print(f"\n{'=' * 72}")
        print(f"  Component: {comp_name}")
        print(f"{'=' * 72}")

        # Funnel
        print("\n  Filter funnel:")
        for label, count in report.filter_funnel:
            print(f"    {label:<50} {count:>5} tools")

        # Unmatched notes
        if report.unmatched_notes:
            print("\n  Unmatched constraints (manual review needed):")
            for note in report.unmatched_notes:
                print(f"    - {note}")

        # Scored tools table
        if not report.scored_tools:
            print("\n  No tools survived filtering.")
            continue

        print(
            f"\n  Top {len(report.scored_tools)} candidates "
            f"({report.coherence_hits} with stack coherence):\n"
        )
        print(
            f"    {'Rank':<5} {'Tool':<30} {'Score':>6} {'Fit':>6} "
            f"{'Pref':>6} {'Coher':>6} {'Current':>8}"
        )
        print(f"    {'-' * 5} {'-' * 30} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 8}")

        for i, sc in enumerate(report.scored_tools, 1):
            current_marker = " *" if sc.is_current else ""
            print(
                f"    {i:<5} {sc.tool_name:<30} {sc.combined_score:>6.1f} "
                f"{sc.fitness_score:>6.1f} {sc.preference_score:>6.1f} "
                f"{sc.coherence_score:>6.1f} {current_marker:>8}"
            )

        print()
