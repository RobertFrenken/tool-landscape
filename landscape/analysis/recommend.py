"""Recommendation engine: suggest tools based on neighborhoods, edges, and fitness."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class Recommendation:
    """A single tool recommendation with context."""

    tool_name: str
    score: float  # 0-100 combined relevance score
    reason: str
    neighborhood: str | None = None
    fitness_score: float | None = None
    relationship: str | None = None  # edge type if direct edge exists


def recommend_for_tool(
    con: duckdb.DuckDBPyConnection,
    tool_name: str,
    *,
    top_n: int = 10,
) -> list[Recommendation]:
    """Recommend tools related to a given tool.

    Priority: direct edges > same neighborhood > shared categories.
    """
    tool = con.execute(
        "SELECT tool_id, name, categories FROM tools WHERE lower(name) = lower($1)",
        [tool_name],
    ).fetchone()
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found")

    tool_id, name, categories = tool
    seen: set[str] = {name}
    recs: list[Recommendation] = []

    # 1. Direct edges (strongest signal)
    edge_rows = con.execute(
        """
        SELECT t.name, e.relation, t.tool_id
        FROM edges e JOIN tools t ON e.target_id = t.tool_id
        WHERE e.source_id = $1
        UNION
        SELECT t.name, e.relation, t.tool_id
        FROM edges e JOIN tools t ON e.source_id = t.tool_id
        WHERE e.target_id = $1
        """,
        [tool_id],
    ).fetchall()

    # Weight by edge type
    edge_weights = {
        "requires": 95,
        "wraps": 90,
        "often_paired": 85,
        "feeds_into": 75,
        "replaces": 70,
        "integrates_with": 50,
    }

    for other_name, relation, other_id in edge_rows:
        if other_name in seen:
            continue
        seen.add(other_name)
        score = edge_weights.get(relation, 50)
        recs.append(
            Recommendation(
                tool_name=other_name,
                score=float(score),
                reason=f"{relation} edge",
                relationship=relation,
            )
        )

    # 2. Same neighborhood
    nbr_row = con.execute(
        """
        SELECT n.name, n.neighborhood_id
        FROM neighborhood_members nm
        JOIN neighborhoods n ON nm.neighborhood_id = n.neighborhood_id
        JOIN tools t ON nm.tool_id = t.tool_id
        WHERE lower(t.name) = lower($1)
        """,
        [tool_name],
    ).fetchone()

    if nbr_row:
        nbr_name, nbr_id = nbr_row
        nbr_tools = con.execute(
            """
            SELECT t.name FROM neighborhood_members nm
            JOIN tools t ON nm.tool_id = t.tool_id
            WHERE nm.neighborhood_id = $1
            """,
            [nbr_id],
        ).fetchall()
        for (other_name,) in nbr_tools:
            if other_name in seen:
                continue
            seen.add(other_name)
            recs.append(
                Recommendation(
                    tool_name=other_name,
                    score=40.0,
                    reason="same neighborhood",
                    neighborhood=nbr_name,
                )
            )

    # 3. Shared categories (weakest signal)
    if categories:
        for cat in categories[:3]:  # limit to top 3 categories
            cat_tools = con.execute(
                """
                SELECT name FROM tools
                WHERE list_contains(categories, $1)
                ORDER BY name
                LIMIT 20
                """,
                [cat],
            ).fetchall()
            for (other_name,) in cat_tools:
                if other_name in seen:
                    continue
                seen.add(other_name)
                recs.append(
                    Recommendation(
                        tool_name=other_name,
                        score=20.0,
                        reason=f"shared category: {cat}",
                    )
                )

    recs.sort(key=lambda r: r.score, reverse=True)
    return recs[:top_n]


def recommend_for_capability(
    con: duckdb.DuckDBPyConnection,
    project_name: str,
    capability_name: str,
    *,
    top_n: int = 10,
) -> list[Recommendation]:
    """Recommend tools for a specific capability using fitness scores + neighborhoods."""
    # Get capability and current tool
    cap = con.execute(
        """
        SELECT c.capability_id, c.current_tool_id, t.name as current_tool_name
        FROM capabilities c
        JOIN projects p ON c.project_id = p.project_id
        LEFT JOIN tools t ON c.current_tool_id = t.tool_id
        WHERE lower(p.name) = lower($1) AND lower(c.name) = lower($2)
        """,
        [project_name, capability_name],
    ).fetchone()
    if not cap:
        raise ValueError(f"Capability '{capability_name}' not found for project '{project_name}'")

    cap_id, current_tool_id, current_tool_name = cap

    # Get fitness scores for this capability
    fitness_rows = con.execute(
        """
        SELECT t.name, f.overall_fitness, f.ceiling_coverage
        FROM fitness f
        JOIN tools t ON f.tool_id = t.tool_id
        WHERE f.capability_id = $1
        ORDER BY f.overall_fitness DESC
        LIMIT $2
        """,
        [cap_id, top_n * 2],
    ).fetchall()

    recs: list[Recommendation] = []
    seen: set[str] = set()
    if current_tool_name:
        seen.add(current_tool_name)

    for name, fitness, ceiling_cov in fitness_rows:
        if name in seen:
            continue
        seen.add(name)

        # Check if tool is in same neighborhood as current tool
        nbr_name = None
        if current_tool_id:
            nbr_row = con.execute(
                """
                SELECT n.name FROM neighborhood_members nm1
                JOIN neighborhood_members nm2 ON nm1.neighborhood_id = nm2.neighborhood_id
                JOIN neighborhoods n ON nm1.neighborhood_id = n.neighborhood_id
                WHERE nm1.tool_id = $1 AND nm2.tool_id = (
                    SELECT tool_id FROM tools WHERE lower(name) = lower($2)
                )
                """,
                [current_tool_id, name],
            ).fetchone()
            if nbr_row:
                nbr_name = nbr_row[0]

        reason_parts = [f"fitness={fitness:.1f}%"]
        if nbr_name:
            reason_parts.append(f"neighborhood={nbr_name}")

        recs.append(
            Recommendation(
                tool_name=name,
                score=fitness,
                reason=", ".join(reason_parts),
                neighborhood=nbr_name,
                fitness_score=fitness,
            )
        )

    recs.sort(key=lambda r: r.score, reverse=True)
    return recs[:top_n]


def recommend_stack(
    con: duckdb.DuckDBPyConnection,
    project_name: str,
    *,
    top_n: int = 5,
) -> dict[str, list[Recommendation]]:
    """Recommend tools for all capabilities in a project."""
    caps = con.execute(
        """
        SELECT c.name FROM capabilities c
        JOIN projects p ON c.project_id = p.project_id
        WHERE lower(p.name) = lower($1)
        ORDER BY c.name
        """,
        [project_name],
    ).fetchall()

    if not caps:
        raise ValueError(f"Project '{project_name}' not found or has no capabilities")

    results: dict[str, list[Recommendation]] = {}
    for (cap_name,) in caps:
        results[cap_name] = recommend_for_capability(con, project_name, cap_name, top_n=top_n)

    return results
