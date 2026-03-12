"""Shopping/matching engine: two-phase pipeline (filter + score) for tool selection.

Given a ProjectSpec, filters tools by hard constraints via DuckDB SQL, then scores
survivors by fitness, weighted preferences, and stack coherence.

Phase C adds:
- propagate_constraints(): transitive requires/wraps closure + replaces exclusions
- _compute_coherence_score(): refactored to accept generic reference_tools
- migration_roi(): ROI ratio per component from MigrationEconomics
- shop_stack(): evaluate candidate stacks as complete units (StackScore)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import combinations

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

# Ordinal ceiling levels for time-horizon scoring (lowest → highest)
CEILING_LEVELS = ["low", "medium", "high", "extensive"]

# Friction level → inverse score (1.0 = no friction)
FRICTION_SCORES = {"low": 1.0, "medium": 0.5, "high": 0.0}


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


@dataclass
class ScoreEvidence:
    """One piece of evidence contributing to a sub-score."""

    dimension: str  # "fitness", "coherence", "friction", "roi", "time_horizon"
    component: str  # which component or boundary this relates to
    detail: str  # human-readable explanation
    value: float  # the numeric contribution


@dataclass
class StackScore:
    """Result of evaluating a named candidate stack as a complete unit."""

    stack_name: str
    tools: dict[str, str | None]  # component → tool_name (or None)
    per_tool_fitness: dict[str, float]  # component → fitness 0-1
    avg_fitness: float
    internal_coherence: float  # 0-1  (1 = fully connected)
    boundary_friction: float  # 0-1  (1 = no friction)
    migration_roi: float  # 0-1
    time_horizon_fit: float  # 0-1
    total_score: float  # weighted combination
    constraint_violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: list[ScoreEvidence] = field(default_factory=list)  # explainability trail


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
    con: duckdb.DuckDBPyConnection, tool_id: int, reference_tools: list[str]
) -> float:
    """Score coherence of tool_id against a reference set of tool names.

    When evaluating candidate stacks: reference_tools = other tools in that stack.
    When evaluating individual tools (legacy): reference_tools = stack_pins.

    Returns 0-1.
    """
    if not reference_tools:
        return 0.0

    # Resolve reference tool names to IDs
    placeholders = ", ".join(["?"] * len(reference_tools))
    ref_rows = con.execute(
        f"SELECT tool_id, name FROM tools WHERE lower(name) IN ({placeholders})",
        [p.lower() for p in reference_tools],
    ).fetchall()
    ref_ids = {row[0]: row[1] for row in ref_rows}

    if not ref_ids:
        return 0.0

    total_score = 0.0

    for ref_id in ref_ids:
        # Check for direct edge (either direction)
        edge = con.execute(
            """
            SELECT relation, weight FROM edges
            WHERE (source_id = ? AND target_id = ?)
               OR (source_id = ? AND target_id = ?)
            """,
            [tool_id, ref_id, ref_id, tool_id],
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
                [tool_id, ref_id],
            ).fetchone()[0]
            if shared > 0:
                total_score += NEIGHBORHOOD_COHERENCE

    # Normalize to 0-1
    max_possible = len(ref_ids)
    if max_possible == 0:
        return 0.0
    return min(1.0, total_score / max_possible)


# ── C1: Constraint Propagation ──────────────────────────────────────────────


def propagate_constraints(
    con: duckdb.DuckDBPyConnection,
    partial_selection: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Given selected tools, compute must-include and must-exclude sets.

    Uses edges table:
    - requires: if A selected and A requires B, B must be included (transitive)
    - wraps: if A selected and A wraps B, B must be included (transitive)
    - replaces: if A selected and A replaces B, B must be excluded from same slot

    Args:
        con: DuckDB connection.
        partial_selection: mapping of component_name → tool_name for currently
            selected tools.

    Returns:
        (must_include_tool_names, must_exclude_tool_names) — both are sets of
        tool names (lower-cased for comparison).
    """
    selected_names = [name for name in partial_selection.values() if name]
    if not selected_names:
        return set(), set()

    # Resolve selected tool names → IDs
    placeholders = ", ".join(["?"] * len(selected_names))
    id_rows = con.execute(
        f"SELECT tool_id, lower(name) FROM tools WHERE lower(name) IN ({placeholders})",
        [n.lower() for n in selected_names],
    ).fetchall()
    selected_ids = {row[0] for row in id_rows}
    selected_id_names = {row[0]: row[1] for row in id_rows}  # id → lower name

    if not selected_ids:
        return set(), set()

    # ── Transitive closure: requires + wraps (must-include) ──────────────────
    # Build seed set from selected IDs, then expand transitively via DuckDB CTE.
    seed_list = list(selected_ids)
    seed_placeholders = ", ".join(str(i) for i in seed_list)

    # Recursive CTE: walk requires and wraps edges starting from seed set
    must_include_rows = con.execute(
        f"""
        WITH RECURSIVE reachable(tool_id) AS (
            -- seed: the directly selected tools
            SELECT unnest(ARRAY[{seed_placeholders}]::INTEGER[])
            UNION
            -- expand via requires / wraps edges
            SELECT e.target_id
            FROM edges e
            JOIN reachable r ON e.source_id = r.tool_id
            WHERE CAST(e.relation AS VARCHAR) IN ('requires', 'wraps')
        )
        SELECT lower(t.name)
        FROM reachable rc
        JOIN tools t ON t.tool_id = rc.tool_id
        WHERE rc.tool_id NOT IN ({seed_placeholders})
        """
    ).fetchall()

    must_include: set[str] = {row[0] for row in must_include_rows}

    # ── Direct replaces edges (must-exclude) ─────────────────────────────────
    # For each selected tool, if it has a `replaces` edge to another tool,
    # that other tool must be excluded (they fill the same slot).
    # NOTE: we do NOT filter out seed IDs here — if a selected tool replaces
    # another selected tool, that IS a conflict that shop_stack needs to flag.
    must_exclude_rows = con.execute(
        f"""
        SELECT lower(t.name)
        FROM edges e
        JOIN tools t ON t.tool_id = e.target_id
        WHERE e.source_id IN ({seed_placeholders})
          AND CAST(e.relation AS VARCHAR) = 'replaces'
        """
    ).fetchall()

    must_exclude: set[str] = {row[0] for row in must_exclude_rows}

    # must_include should not contain tools that are also excluded
    must_include -= must_exclude

    return must_include, must_exclude


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
        coherence_raw = _compute_coherence_score(
            con, tool_id, stack_pins
        )  # legacy: uses stack_pins
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


# ── C3: Migration ROI ──────────────────────────────────────────────────────


def migration_roi(spec: ProjectSpec) -> dict[str, float]:
    """Compute migration ROI signal per component.

    ratio = (hours_per_week × 52) / effort_hours

    Trend adjustment (applied before mapping):
      - "increasing": ratio × 1.3  (friction will get worse, more urgency to migrate)
      - "decreasing": ratio × 0.7  (friction improving, less urgency)
      - "stable":     no adjustment

    Continuous signal mapping (linear, capped at 1.0):
      signal = min(1.0, ratio / 10.0)

    Examples: ratio 0 → 0.0, ratio 5 → 0.5, ratio 10+ → 1.0

    Components without migration data return 0.5 (neutral).

    Args:
        spec: ProjectSpec with optional migration field.

    Returns:
        Dict of component_name → ROI signal (0-1).
    """
    if spec.migration is None:
        return {}

    mg = spec.migration
    result: dict[str, float] = {}

    all_components = set(mg.one_time) | set(mg.ongoing_friction)

    for comp in all_components:
        one_time = mg.one_time.get(comp)
        friction = mg.ongoing_friction.get(comp)

        if one_time is None or friction is None:
            # Partial data → neutral
            result[comp] = 0.5
            continue

        effort = one_time.effort_hours
        if effort <= 0:
            result[comp] = 1.0  # free migration → strong migrate
            continue

        annual_friction = friction.hours_per_week * 52.0
        ratio = annual_friction / effort

        # Apply trend adjustment
        if friction.trend == "increasing":
            ratio *= 1.3
        elif friction.trend == "decreasing":
            ratio *= 0.7
        # "stable" → no adjustment

        # Continuous linear mapping, capped at 1.0
        result[comp] = min(1.0, ratio / 10.0)

    return result


# ── C4: Stack Evaluation ────────────────────────────────────────────────────


def _stack_internal_coherence(
    con: duckdb.DuckDBPyConnection,
    tool_names: list[str],
    collect_evidence: bool = False,
) -> tuple[float, list[ScoreEvidence]]:
    """Compute pairwise internal coherence for all tools in a stack.

    Score = (coherent pairs) / (total possible pairs).
    A pair is "coherent" if it has a COHERENCE_EDGE_TYPES edge or shared neighborhood.
    Returns (score 0-1, evidence list). Returns (0.5, []) if fewer than 2 tools.
    """
    evidence: list[ScoreEvidence] = []
    valid_names = [n for n in tool_names if n]
    if len(valid_names) < 2:
        return 0.5, evidence  # trivially coherent, but not measurable

    # Resolve names → ids + canonical display names
    placeholders = ", ".join(["?"] * len(valid_names))
    rows = con.execute(
        f"SELECT tool_id, lower(name), name FROM tools WHERE lower(name) IN ({placeholders})",
        [n.lower() for n in valid_names],
    ).fetchall()
    id_map = {row[1]: row[0] for row in rows}  # lower_name → id
    display_map = {row[1]: row[2] for row in rows}  # lower_name → display name

    tool_ids = [id_map[n.lower()] for n in valid_names if n.lower() in id_map]
    id_to_display = {id_map[k]: display_map[k] for k in id_map}

    if len(tool_ids) < 2:
        return 0.0, evidence

    total_pairs = 0
    coherent_pairs = 0

    for a_id, b_id in combinations(tool_ids, 2):
        total_pairs += 1
        a_name = id_to_display.get(a_id, str(a_id))
        b_name = id_to_display.get(b_id, str(b_id))
        pair_label = f"{a_name} \u2194 {b_name}"

        # Direct coherent edge
        edge_rows = con.execute(
            """
            SELECT relation, weight FROM edges
            WHERE (source_id = ? AND target_id = ?)
               OR (source_id = ? AND target_id = ?)
            """,
            [a_id, b_id, b_id, a_id],
        ).fetchall()

        found_coherent = False
        for relation, weight in edge_rows:
            rel_str = str(relation)
            if rel_str in COHERENCE_EDGE_TYPES:
                coherent_pairs += 1
                found_coherent = True
                wt = weight or 1.0
                if collect_evidence:
                    evidence.append(
                        ScoreEvidence(
                            dimension="coherence",
                            component=pair_label,
                            detail=f"direct edge: {rel_str} (weight {wt:.1f})",
                            value=min(1.0, wt / 3.0),
                        )
                    )
                break

        if not found_coherent:
            shared_rows = con.execute(
                """
                SELECT n.name FROM neighborhood_members nm1
                JOIN neighborhood_members nm2
                  ON nm1.neighborhood_id = nm2.neighborhood_id
                JOIN neighborhoods n ON n.neighborhood_id = nm1.neighborhood_id
                WHERE nm1.tool_id = ? AND nm2.tool_id = ?
                LIMIT 1
                """,
                [a_id, b_id],
            ).fetchall()
            if shared_rows:
                coherent_pairs += 1
                nbr_name = shared_rows[0][0]
                if collect_evidence:
                    evidence.append(
                        ScoreEvidence(
                            dimension="coherence",
                            component=pair_label,
                            detail=f'shared neighborhood: "{nbr_name}"',
                            value=NEIGHBORHOOD_COHERENCE,
                        )
                    )
            elif collect_evidence:
                evidence.append(
                    ScoreEvidence(
                        dimension="coherence",
                        component=pair_label,
                        detail="no connection found",
                        value=0.0,
                    )
                )

    if total_pairs == 0:
        return 0.5, evidence
    return coherent_pairs / total_pairs, evidence


def _stack_boundary_friction(
    spec: ProjectSpec, stack_tools: dict[str, str | None], stack_name: str = ""
) -> float:
    """Compute boundary friction score for the stack.

    Maps friction levels from spec.data_flow.boundaries:
      low=1.0, medium=0.5, high=0.0

    Per-stack overrides in spec.stack_boundary_overrides[stack_name] take precedence
    over the default boundary friction level for matching (between) pairs.

    Returns average across all boundaries. If no data_flow, returns 0.5 (neutral).
    """
    if spec.data_flow is None or not spec.data_flow.boundaries:
        return 0.5

    # Build override lookup: frozenset({a, b}) → friction level
    overrides: dict[frozenset, str] = {}
    if stack_name and spec.stack_boundary_overrides:
        for ovr in spec.stack_boundary_overrides.get(stack_name, []):
            key = frozenset(ovr.between)
            overrides[key] = ovr.friction

    scores = []
    for boundary in spec.data_flow.boundaries:
        key = frozenset(boundary.between)
        friction_level = overrides.get(key, boundary.friction)
        scores.append(FRICTION_SCORES.get(friction_level, 0.5))

    return sum(scores) / len(scores) if scores else 0.5


def _stack_time_horizon_fit(
    spec: ProjectSpec, stack_tools: dict[str, str | None], con: duckdb.DuckDBPyConnection
) -> float:
    """Score how well the stack meets ceiling requirements by the timeline.

    For each component with a ceiling_timeline:
    - If timeline is "2026-*" (near-term): tool must have high or extensive ceiling → 1.0,
      medium → 0.5, anything else → 0.0
    - If timeline is "2027+" (far-term): medium+ ceiling is sufficient → 1.0

    Returns average across components with ceiling_timeline data. Defaults to 1.0 if none.
    """
    if spec.time_horizon is None or not spec.time_horizon.ceiling_timeline:
        return 1.0

    scores = []

    for comp_name, timeline in spec.time_horizon.ceiling_timeline.items():
        tool_name = stack_tools.get(comp_name)
        if not tool_name:
            scores.append(0.5)  # unknown tool → neutral
            continue

        row = con.execute(
            "SELECT CAST(capability_ceiling AS VARCHAR) FROM tools WHERE lower(name) = lower(?)",
            [tool_name],
        ).fetchone()

        if not row or not row[0]:
            scores.append(0.0)  # tool not in DB or no ceiling info
            continue

        ceiling = row[0]  # e.g. "low", "medium", "high", "extensive"
        ceiling_idx = CEILING_LEVELS.index(ceiling) if ceiling in CEILING_LEVELS else -1

        # Determine timeline urgency: "2027" or higher year → far-term
        far_term = False
        try:
            year_str = timeline.split("-")[0].split("Q")[0].strip()
            far_term = int(year_str) >= 2027
        except (ValueError, IndexError):
            pass

        if far_term:
            # medium or better is fine for far-term
            scores.append(1.0 if ceiling_idx >= CEILING_LEVELS.index("medium") else 0.5)
        else:
            # near-term: high or extensive needed
            if ceiling_idx >= CEILING_LEVELS.index("high"):
                scores.append(1.0)
            elif ceiling_idx == CEILING_LEVELS.index("medium"):
                scores.append(0.5)
            else:
                scores.append(0.0)

    return sum(scores) / len(scores) if scores else 1.0



def generate_candidate_stacks(
    con: duckdb.DuckDBPyConnection,
    spec: ProjectSpec,
    top_n_per_slot: int = 3,
    max_stacks: int = 10,
) -> dict[str, dict[str, str | None]]:
    """Auto-generate candidate stacks from v1 per-slot evaluation.

    Pipeline:
    1. Run shop() per component to get top_n_per_slot winners per slot.
    2. Always include "current" stack from spec.components[*].current_tool.
    3. Build "v1_consensus" from top-1 of each slot.
    4. Build "variant_{component}_{rank}" stacks by swapping one slot at a time
       with the #2 or #3 winner (where they differ from #1).
    5. Prune stacks where propagate_constraints() finds violations.
    6. Cap at max_stacks total (current + v1_consensus + variants).

    For components where v1 returns 0 candidates (filter too restrictive), the
    current_tool value is kept (or None if unspecified).

    Args:
        con: DuckDB connection.
        spec: ProjectSpec with components defined.
        top_n_per_slot: How many winners to consider per component slot.
        max_stacks: Maximum total stacks to return (including "current").

    Returns:
        Dict of stack_name → {component → tool_name | None}, suitable for
        assigning to spec.candidate_stacks before calling shop_stack().
    """
    # ── Step 1: Run shop() per component to get ranked candidates ────────────
    reports = shop(con, spec, top_n=top_n_per_slot)

    # Per-component ranked tool names (top_n_per_slot entries, may be < top_n)
    slot_winners: dict[str, list[str | None]] = {}
    for comp_name, comp in spec.components.items():
        report = reports.get(comp_name)
        ranked: list[str | None] = []
        if report and report.scored_tools:
            ranked = [s.tool_name for s in report.scored_tools[:top_n_per_slot]]
        # Pad with current_tool if no results
        if not ranked:
            ranked = [comp.current_tool]
        slot_winners[comp_name] = ranked

    # ── Step 2: "current" stack ───────────────────────────────────────────────
    current_stack: dict[str, str | None] = {
        comp_name: comp.current_tool
        for comp_name, comp in spec.components.items()
    }

    # ── Step 3: "v1_consensus" — top-1 from each slot ────────────────────────
    consensus_stack: dict[str, str | None] = {
        comp_name: (winners[0] if winners else None)
        for comp_name, winners in slot_winners.items()
    }

    # ── Step 4: Variant stacks (swap one slot at a time) ─────────────────────
    variants: list[tuple[str, dict[str, str | None]]] = []

    for comp_name, winners in slot_winners.items():
        for rank_idx in range(1, min(top_n_per_slot, len(winners))):
            alt_tool = winners[rank_idx]
            top_tool = winners[0] if winners else None
            # Only create variant if it actually differs from consensus
            if alt_tool == top_tool:
                continue
            variant = dict(consensus_stack)
            variant[comp_name] = alt_tool
            variant_name = f"variant_{comp_name}_{rank_idx + 1}"
            variants.append((variant_name, variant))

    # ── Step 5: Prune invalid combos via constraint propagation ──────────────
    def _is_valid(stack: dict[str, str | None]) -> bool:
        must_inc, must_exc = propagate_constraints(con, stack)
        stack_lower = {(v.lower() if v else None) for v in stack.values()}
        # Violations: missing required dep or conflicting tool present
        for tool in must_inc:
            if tool not in stack_lower:
                return False
        for tool in must_exc:
            if tool in stack_lower:
                return False
        return True

    # ── Step 6: Assemble and cap at max_stacks ────────────────────────────────
    result: dict[str, dict[str, str | None]] = {}

    # "current" always first
    result["current"] = current_stack

    # v1_consensus if it's different from current (or if current is all-None)
    if len(result) < max_stacks:
        result["v1_consensus"] = consensus_stack

    # Variants — filter out duplicates and invalid combos
    seen_fingerprints: set[str] = {str(sorted(s.items())) for s in result.values()}

    for variant_name, variant_stack in variants:
        if len(result) >= max_stacks:
            break
        fp = str(sorted(variant_stack.items()))
        if fp in seen_fingerprints:
            continue
        if not _is_valid(variant_stack):
            continue
        seen_fingerprints.add(fp)
        result[variant_name] = variant_stack

    return result


def shop_stack(
    con: duckdb.DuckDBPyConnection,
    spec: ProjectSpec,
    collect_evidence: bool = False,
) -> dict[str, StackScore]:
    """Evaluate candidate stacks as complete units.

    For each candidate stack in spec.candidate_stacks:
    1. Propagate constraints — detect violations (does not prune, records them)
    2. Per-tool fitness — existing score_tool_capability() per slot
    3. Internal coherence — pairwise between all tools in the stack
    4. Boundary friction — from spec.data_flow.boundaries
    5. Migration ROI — from spec.migration
    6. Time-horizon fit — does stack meet ceiling by timeline?

    Combined stack score:
      stack_score = (
          avg(per_tool_fitness) × 0.30
        + internal_coherence    × 0.25
        + boundary_friction     × 0.15
        + migration_roi_signal  × 0.15
        + time_horizon_fit      × 0.15
      )

    Args:
        con: DuckDB connection.
        spec: ProjectSpec with candidate_stacks (and optionally v2 fields).
        collect_evidence: If True, populate StackScore.evidence with per-dimension trails.

    Returns:
        Dict of stack_name → StackScore, sorted by total_score descending.
    """
    if not spec.candidate_stacks:
        return {}

    roi_signals = migration_roi(spec)

    results: dict[str, StackScore] = {}

    for stack_name, stack_tools in spec.candidate_stacks.items():
        violations: list[str] = []
        notes_list: list[str] = []
        ev: list[ScoreEvidence] = []

        # ── 1. Constraint propagation check ─────────────────────────────────
        must_include, must_exclude = propagate_constraints(con, stack_tools)

        stack_lower = {(v.lower() if v else None) for v in stack_tools.values()}

        for tool in must_include:
            if tool not in stack_lower:
                violations.append(f"Missing required dependency: '{tool}' (via requires/wraps)")

        for tool in must_exclude:
            if tool in stack_lower:
                violations.append(
                    f"Conflict: '{tool}' is excluded by a replaces edge but present in stack"
                )

        # ── 2. Per-tool fitness ──────────────────────────────────────────────
        per_tool_fitness: dict[str, float] = {}

        for comp_name, tool_name in stack_tools.items():
            if not tool_name:
                per_tool_fitness[comp_name] = 0.0
                notes_list.append(f"{comp_name}: no tool specified")
                continue

            tool_row = con.execute(
                "SELECT * FROM tools WHERE lower(name) = lower(?)", [tool_name]
            ).fetchone()

            if not tool_row:
                per_tool_fitness[comp_name] = 0.0
                notes_list.append(f"{comp_name}: '{tool_name}' not in database")
                continue

            cols = [desc[0] for desc in con.description]
            tool_dict = dict(zip(cols, tool_row))
            tool_id = tool_dict["tool_id"]

            # Build synthetic capability from the component spec (if present)
            comp_spec = spec.components.get(comp_name)
            if comp_spec is not None:
                synthetic_cap = _build_synthetic_capability(comp_spec)
            else:
                # No component spec → minimal capability (just checks exist)
                synthetic_cap = {
                    "capability_id": -1,
                    "name": comp_name,
                    "floor_requirements": "{}",
                    "ceiling_requirements": {},
                }

            metrics = get_latest_metrics(con, tool_id)
            fitness_result = score_tool_capability(tool_dict, synthetic_cap, metrics)
            fit_val = fitness_result.overall_fitness / 100.0
            per_tool_fitness[comp_name] = fit_val

            if collect_evidence:
                ceiling = str(tool_dict.get("capability_ceiling") or "unknown")
                momentum = str(tool_dict.get("community_momentum") or "unknown")
                ev.append(
                    ScoreEvidence(
                        dimension="fitness",
                        component=comp_name,
                        detail=(
                            f"{tool_name}: fitness={fit_val:.2f} "
                            f"(ceiling={ceiling}, momentum={momentum})"
                        ),
                        value=fit_val,
                    )
                )

        avg_fitness = (
            sum(per_tool_fitness.values()) / len(per_tool_fitness) if per_tool_fitness else 0.0
        )

        # ── 3. Internal coherence ────────────────────────────────────────────
        all_tool_names = [v for v in stack_tools.values() if v]
        coherence, coherence_ev = _stack_internal_coherence(
            con, all_tool_names, collect_evidence=collect_evidence
        )
        if collect_evidence:
            ev.extend(coherence_ev)

        # ── 4. Boundary friction ─────────────────────────────────────────────
        friction = _stack_boundary_friction(spec, stack_tools, stack_name)

        if collect_evidence and spec.data_flow is not None:
            for _bnd in spec.data_flow.boundaries:
                _fric_score = FRICTION_SCORES.get(_bnd.friction, 0.5)
                _stage_a, _stage_b = _bnd.between
                _notes_text = f" — {_bnd.notes}" if _bnd.notes else ""
                ev.append(
                    ScoreEvidence(
                        dimension="friction",
                        component=f"{_stage_a} \u2192 {_stage_b}",
                        detail=f"friction: {_bnd.friction} ({_fric_score:.2f}){_notes_text}",
                        value=_fric_score,
                    )
                )

        # ── 5. Migration ROI ─────────────────────────────────────────────────
        # Check if this stack is essentially the current stack (all current_tools match)
        current_tools_lower = {
            comp_name: (comp.current_tool or "").lower()
            for comp_name, comp in spec.components.items()
        }
        is_current_stack = all(
            (stack_tools.get(c) or "").lower() == current
            for c, current in current_tools_lower.items()
            if current
        )

        if is_current_stack:
            roi_signal = 0.5  # neutral — no migration needed
            notes_list.append("This stack matches current tools — migration_roi is neutral")
        else:
            # Average ROI signal across components that appear in migration data
            comp_rois = [
                roi_signals[comp_name] for comp_name in stack_tools if comp_name in roi_signals
            ]
            roi_signal = sum(comp_rois) / len(comp_rois) if comp_rois else 0.5

        if collect_evidence and spec.migration is not None:
            for _roi_comp in stack_tools:
                _ot = spec.migration.one_time.get(_roi_comp)
                _fr = spec.migration.ongoing_friction.get(_roi_comp)
                _sig = roi_signals.get(_roi_comp, 0.5)
                if _ot is not None and _fr is not None:
                    _effort = _ot.effort_hours
                    _annual = _fr.hours_per_week * 52.0
                    _ratio = _annual / _effort if _effort > 0 else float("inf")
                    if _ratio > 5.0:
                        _verdict = "strong migrate"
                    elif _ratio >= 2.0:
                        _verdict = "neutral-to-migrate"
                    else:
                        _verdict = "hold"
                    ev.append(
                        ScoreEvidence(
                            dimension="roi",
                            component=_roi_comp,
                            detail=(
                                f"annual friction: {_annual:.0f}h, migration effort: {_effort:.0f}h, "
                                f"ratio: {_ratio:.1f}\u00d7 \u2192 {_verdict}"
                            ),
                            value=_sig,
                        )
                    )

        # ── 6. Time-horizon fit ──────────────────────────────────────────────
        th_fit = _stack_time_horizon_fit(spec, stack_tools, con)

        if collect_evidence and spec.time_horizon is not None:
            for _th_comp, _timeline in spec.time_horizon.ceiling_timeline.items():
                _th_tool = stack_tools.get(_th_comp)
                if not _th_tool:
                    continue
                _th_row = con.execute(
                    "SELECT CAST(capability_ceiling AS VARCHAR) FROM tools WHERE lower(name) = lower(?)",
                    [_th_tool],
                ).fetchone()
                _th_ceiling = _th_row[0] if _th_row and _th_row[0] else "unknown"
                _th_cidx = CEILING_LEVELS.index(_th_ceiling) if _th_ceiling in CEILING_LEVELS else -1
                _far_term = False
                try:
                    _yr = _timeline.split("-")[0].split("Q")[0].strip()
                    _far_term = int(_yr) >= 2027
                except (ValueError, IndexError):
                    pass
                if _far_term:
                    _fit_label = "meets" if _th_cidx >= CEILING_LEVELS.index("medium") else "partial"
                    _th_val = 1.0 if _th_cidx >= CEILING_LEVELS.index("medium") else 0.5
                else:
                    if _th_cidx >= CEILING_LEVELS.index("high"):
                        _fit_label = "meets"
                        _th_val = 1.0
                    elif _th_cidx == CEILING_LEVELS.index("medium"):
                        _fit_label = "partial"
                        _th_val = 0.5
                    else:
                        _fit_label = "misses"
                        _th_val = 0.0
                _th_verdict = "full fit" if _fit_label == "meets" else _fit_label
                ev.append(
                    ScoreEvidence(
                        dimension="time_horizon",
                        component=_th_comp,
                        detail=(
                            f"ceiling '{_th_ceiling}' {_fit_label} {_timeline}"
                            f" deadline \u2192 {_th_verdict}"
                        ),
                        value=_th_val,
                    )
                )

        # ── Combined score ───────────────────────────────────────────────────
        total = (
            avg_fitness * 0.30
            + coherence * 0.25
            + friction * 0.15
            + roi_signal * 0.15
            + th_fit * 0.15
        )

        results[stack_name] = StackScore(
            stack_name=stack_name,
            tools=dict(stack_tools),
            per_tool_fitness=per_tool_fitness,
            avg_fitness=round(avg_fitness, 4),
            internal_coherence=round(coherence, 4),
            boundary_friction=round(friction, 4),
            migration_roi=round(roi_signal, 4),
            time_horizon_fit=round(th_fit, 4),
            total_score=round(total, 4),
            constraint_violations=violations,
            notes=notes_list,
            evidence=ev if collect_evidence else [],
        )

    # Sort by total_score descending
    return dict(sorted(results.items(), key=lambda kv: kv[1].total_score, reverse=True))


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


# ── C5: Stack Pretty-Printer ────────────────────────────────────────────────


def stack_scores_to_json(scores: dict[str, StackScore]) -> str:
    """Serialize StackScore results to JSON string."""
    data = {}
    for name, ss in scores.items():
        data[name] = {
            "stack_name": ss.stack_name,
            "tools": ss.tools,
            "per_tool_fitness": ss.per_tool_fitness,
            "avg_fitness": ss.avg_fitness,
            "internal_coherence": ss.internal_coherence,
            "boundary_friction": ss.boundary_friction,
            "migration_roi": ss.migration_roi,
            "time_horizon_fit": ss.time_horizon_fit,
            "total_score": ss.total_score,
            "constraint_violations": ss.constraint_violations,
            "notes": ss.notes,
            "evidence": [
                {
                    "dimension": e.dimension,
                    "component": e.component,
                    "detail": e.detail,
                    "value": e.value,
                }
                for e in ss.evidence
            ],
        }
    return json.dumps(data, indent=2)


def print_stack_scores(scores: dict[str, StackScore]) -> None:
    """Pretty-print stack evaluation results to stdout.

    Stacks are expected to be pre-sorted by total_score descending.
    The first stack is highlighted as the winner.
    """
    if not scores:
        print("No candidate stacks to evaluate.")
        return

    ranked = list(scores.values())
    winner = ranked[0]

    print(f"\n{'=' * 80}")
    print(f"  Stack Evaluation  ({len(ranked)} candidate{'s' if len(ranked) != 1 else ''})")
    print(f"{'=' * 80}")

    # Summary ranking table
    print(
        f"\n  {'Rank':<5} {'Stack':<25} {'Score':>6} {'Fit':>6} {'Coher':>6} "
        f"{'Fric':>6} {'ROI':>6} {'TH':>6}  Violations"
    )
    print(
        f"  {'-' * 5} {'-' * 25} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6}  {'-' * 12}"
    )

    for rank, ss in enumerate(ranked, 1):
        viol_str = (
            f"{len(ss.constraint_violations)} issue(s)" if ss.constraint_violations else "none"
        )
        winner_mark = " *" if rank == 1 else "  "
        print(
            f"  {rank:<5} {ss.stack_name:<25} {ss.total_score:>6.3f} "
            f"{ss.avg_fitness:>6.3f} {ss.internal_coherence:>6.3f} "
            f"{ss.boundary_friction:>6.3f} {ss.migration_roi:>6.3f} "
            f"{ss.time_horizon_fit:>6.3f} {winner_mark} {viol_str}"
        )

    # Detailed breakdown per stack
    for rank, ss in enumerate(ranked, 1):
        marker = "  [WINNER]" if rank == 1 else ""
        print(f"\n  {'─' * 76}")
        print(f"  {rank}. {ss.stack_name}{marker}")
        print(f"  {'─' * 76}")
        print(f"    Total score: {ss.total_score:.3f}")
        print("    Weights:  fitness×0.30  coherence×0.25  friction×0.15  roi×0.15  th×0.15")
        print()

        # Per-tool fitness
        print(f"    {'Component':<28} {'Tool':<28} {'Fitness':>8}")
        print(f"    {'-' * 28} {'-' * 28} {'-' * 8}")
        for comp_name, tool_name in ss.tools.items():
            fit = ss.per_tool_fitness.get(comp_name, 0.0)
            t_str = tool_name or "(none)"
            print(f"    {comp_name:<28} {t_str:<28} {fit:>7.3f}")

        # Sub-scores
        print()
        print(f"    avg_fitness:        {ss.avg_fitness:.3f}")
        print(f"    internal_coherence: {ss.internal_coherence:.3f}")
        print(f"    boundary_friction:  {ss.boundary_friction:.3f}")
        print(f"    migration_roi:      {ss.migration_roi:.3f}")
        print(f"    time_horizon_fit:   {ss.time_horizon_fit:.3f}")

        if ss.constraint_violations:
            print(f"\n    Constraint violations ({len(ss.constraint_violations)}):")
            for v in ss.constraint_violations:
                print(f"      ! {v}")

        if ss.notes:
            print("\n    Notes:")
            for note in ss.notes:
                print(f"      - {note}")

    print()


def print_stack_evidence(scores: dict[str, StackScore]) -> None:
    """Print per-dimension evidence trails for each stack.

    Only prints stacks that have evidence (i.e., shop_stack was called with
    collect_evidence=True). Groups evidence by dimension for readability.
    """
    if not scores:
        return

    ranked = list(scores.values())
    dims = ["fitness", "coherence", "friction", "roi", "time_horizon"]
    dim_labels = {
        "fitness": "FITNESS",
        "coherence": "COHERENCE",
        "friction": "FRICTION",
        "roi": "MIGRATION ROI",
        "time_horizon": "TIME HORIZON",
    }
    score_fields = {
        "fitness": "avg_fitness",
        "coherence": "internal_coherence",
        "friction": "boundary_friction",
        "roi": "migration_roi",
        "time_horizon": "time_horizon_fit",
    }

    for rank, ss in enumerate(ranked, 1):
        print(f"\n  {rank}. {ss.stack_name}  (score: {ss.total_score:.3f})")

        if not ss.evidence:
            print("     (no evidence — run with collect_evidence=True)")
            continue

        # Group evidence by dimension
        by_dim: dict[str, list[ScoreEvidence]] = {d: [] for d in dims}
        for ev_item in ss.evidence:
            if ev_item.dimension in by_dim:
                by_dim[ev_item.dimension].append(ev_item)

        for dim in dims:
            ev_list = by_dim[dim]
            field = score_fields[dim]
            dim_score = getattr(ss, field, None)
            label = dim_labels[dim]
            score_str = f" ({dim_score:.3f})" if dim_score is not None else ""
            print(f"\n     {label}{score_str}")
            if ev_list:
                # Find max component label width for alignment
                max_w = max(len(e.component) for e in ev_list)
                max_w = max(max_w, 20)
                for ev_item in ev_list:
                    comp_str = f"{ev_item.component:<{max_w}}"
                    print(f"       {comp_str}  {ev_item.detail}")
            else:
                print("       (no data)")

    print()
