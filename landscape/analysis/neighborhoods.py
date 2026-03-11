"""Graph clustering via Louvain community detection."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb
import networkx as nx
from networkx.algorithms.community import louvain_communities

# ── Edge weight table ───────────────────────────────────────────────────────

EDGE_WEIGHTS: dict[str, float] = {
    "requires": 3.0,
    "wraps": 2.5,
    "often_paired": 2.0,
    "feeds_into": 1.5,
    "replaces": 1.0,
    "integrates_with": 0.3,
}


@dataclass
class NeighborhoodResult:
    """A computed community of related tools."""

    name: str
    description: str
    tool_ids: list[int]
    tool_names: list[str]
    dominant_categories: list[str]
    size: int


# ── Graph construction ──────────────────────────────────────────────────────


def build_graph(
    con: duckdb.DuckDBPyConnection,
    edge_weights: dict[str, float] | None = None,
) -> nx.Graph:
    """Build a weighted NetworkX graph from tools and edges tables."""
    weights = edge_weights or EDGE_WEIGHTS
    G = nx.Graph()

    # Load all tools as nodes
    rows = con.execute("SELECT tool_id, name, categories, used_by FROM tools").fetchall()
    cols = [desc[0] for desc in con.description]

    tools_by_id: dict[int, dict] = {}
    for row in rows:
        tool = dict(zip(cols, row))
        tid = tool["tool_id"]
        cats = tool["categories"] or []
        used = tool["used_by"] or []
        G.add_node(tid, name=tool["name"], categories=cats, used_by=used)
        tools_by_id[tid] = tool

    # Load explicit edges
    edge_rows = con.execute("SELECT source_id, target_id, relation FROM edges").fetchall()
    for src, tgt, relation in edge_rows:
        w = weights.get(relation, 0.5)
        if G.has_edge(src, tgt):
            G[src][tgt]["weight"] += w
        else:
            G.add_edge(src, tgt, weight=w)

    # Synthetic edges for connectivity: shared categories and shared projects
    tool_ids = list(tools_by_id.keys())
    # Build category → tool_id index
    cat_index: dict[str, list[int]] = {}
    project_index: dict[str, list[int]] = {}
    for tid, tool in tools_by_id.items():
        for cat in tool["categories"] or []:
            cat_index.setdefault(cat, []).append(tid)
        for proj in tool["used_by"] or []:
            project_index.setdefault(proj, []).append(tid)

    # Shared-category edges (only for isolated nodes — no explicit edges)
    connected_nodes = {n for e in G.edges() for n in e}
    isolated = set(tool_ids) - connected_nodes

    for cat, members in cat_index.items():
        iso_members = [m for m in members if m in isolated]
        for i in range(len(iso_members)):
            for j in range(i + 1, len(iso_members)):
                a, b = iso_members[i], iso_members[j]
                # Count shared categories between a and b
                cats_a = set(tools_by_id[a]["categories"] or [])
                cats_b = set(tools_by_id[b]["categories"] or [])
                shared = len(cats_a & cats_b)
                w = min(0.1 * shared, 0.5)
                if G.has_edge(a, b):
                    G[a][b]["weight"] = max(G[a][b]["weight"], w)
                else:
                    G.add_edge(a, b, weight=w)

    # Shared-project edges (used_by)
    for proj, members in project_index.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += 1.0
                else:
                    G.add_edge(a, b, weight=1.0)

    return G


# ── Community detection ─────────────────────────────────────────────────────


def compute_neighborhoods(
    con: duckdb.DuckDBPyConnection,
    resolution: float = 1.0,
    edge_weights: dict[str, float] | None = None,
    min_size: int = 3,
) -> list[NeighborhoodResult]:
    """Run Louvain community detection and return neighborhood results."""
    G = build_graph(con, edge_weights)

    communities = louvain_communities(G, weight="weight", resolution=resolution, seed=42)

    # Split into keepers and orphans
    keepers = [c for c in communities if len(c) >= min_size]
    orphans = [c for c in communities if len(c) < min_size]

    if orphans and keepers:
        keepers = _assign_orphans(G, keepers, orphans)

    # Build results
    existing_names: set[str] = set()
    results: list[NeighborhoodResult] = []

    for community in keepers:
        tool_ids = sorted(community)
        tool_names = [G.nodes[tid]["name"] for tid in tool_ids]
        tool_categories = [G.nodes[tid].get("categories", []) for tid in tool_ids]

        name = _generate_name(tool_names, tool_categories, existing_names)
        existing_names.add(name)

        # Dominant categories
        cat_counts: Counter[str] = Counter()
        for cats in tool_categories:
            cat_counts.update(cats)
        dominant = [cat for cat, _ in cat_counts.most_common(3)]

        desc = f"{len(tool_ids)} tools; top categories: {', '.join(dominant)}"

        results.append(
            NeighborhoodResult(
                name=name,
                description=desc,
                tool_ids=tool_ids,
                tool_names=tool_names,
                dominant_categories=dominant,
                size=len(tool_ids),
            )
        )

    results.sort(key=lambda r: r.size, reverse=True)
    return results


# ── Internal helpers ────────────────────────────────────────────────────────


def _generate_name(
    tool_names: list[str],
    tool_categories: list[list[str]],
    existing_names: set[str],
) -> str:
    """Generate a neighborhood name from dominant categories."""
    cat_counts: Counter[str] = Counter()
    for cats in tool_categories:
        cat_counts.update(cats)

    if not cat_counts:
        base = "misc"
    else:
        top = cat_counts.most_common(2)
        base = top[0][0]

        if base in existing_names and len(top) > 1:
            base = f"{base}-{top[1][0]}"

    # Disambiguate with counter if still colliding
    name = base
    counter = 2
    while name in existing_names:
        name = f"{base}-{counter}"
        counter += 1

    return name


def _assign_orphans(
    G: nx.Graph,
    communities: list[set[int]],
    orphan_sets: list[set[int]],
) -> list[set[int]]:
    """Merge orphan sets into the community with the most connecting edges."""
    for orphan_set in orphan_sets:
        best_idx = 0
        best_score = -1

        for idx, community in enumerate(communities):
            score = 0
            for node in orphan_set:
                for neighbor in G.neighbors(node):
                    if neighbor in community:
                        score += G[node][neighbor].get("weight", 1.0)
            if score > best_score:
                best_score = score
                best_idx = idx

        communities[best_idx] = communities[best_idx] | orphan_set

    return communities


# ── Persistence ─────────────────────────────────────────────────────────────


def persist_neighborhoods(
    con: duckdb.DuckDBPyConnection,
    results: list[NeighborhoodResult],
    respect_pins: bool = True,
) -> int:
    """Write neighborhood results to DB, optionally preserving pinned memberships."""
    # Read existing pinned memberships before clearing
    pinned: list[tuple[str, int]] = []
    if respect_pins:
        pinned_rows = con.execute(
            """
            SELECT n.name, nm.tool_id
            FROM neighborhood_members nm
            JOIN neighborhoods n ON n.neighborhood_id = nm.neighborhood_id
            WHERE nm.pinned = true
            """
        ).fetchall()
        pinned = [(name, tool_id) for name, tool_id in pinned_rows]

    # Clear existing computed data
    con.execute("DELETE FROM neighborhood_members")
    con.execute("DELETE FROM neighborhoods")

    now = datetime.now(UTC).isoformat()

    # Insert neighborhoods and members
    for result in results:
        params_json = json.dumps({"algorithm": "louvain_v1", "resolution": 1.0})
        con.execute(
            """
            INSERT INTO neighborhoods
                (name, description, origin, algorithm, parameters, computed_at)
            VALUES ($1, $2, $3::neighborhood_origin, $4, $5::JSON, $6::TIMESTAMP)
            """,
            [result.name, result.description, "computed", "louvain_v1", params_json, now],
        )
        nbr_id = con.execute(
            "SELECT neighborhood_id FROM neighborhoods WHERE name = $1", [result.name]
        ).fetchone()[0]

        for tool_id in result.tool_ids:
            con.execute(
                """
                INSERT INTO neighborhood_members (neighborhood_id, tool_id, membership, pinned)
                VALUES ($1, $2, $3, $4)
                """,
                [nbr_id, tool_id, 1.0, False],
            )

    # Restore pinned memberships
    if pinned:
        # Build name → neighborhood_id map for new neighborhoods
        nbr_map: dict[str, int] = {}
        for row in con.execute("SELECT name, neighborhood_id FROM neighborhoods").fetchall():
            nbr_map[row[0]] = row[1]

        for pin_name, pin_tool_id in pinned:
            if pin_name in nbr_map:
                target_nbr_id = nbr_map[pin_name]
            else:
                # Original neighborhood gone — find which new one has this tool
                existing = con.execute(
                    """
                    SELECT neighborhood_id FROM neighborhood_members
                    WHERE tool_id = $1 LIMIT 1
                    """,
                    [pin_tool_id],
                ).fetchone()
                if existing:
                    target_nbr_id = existing[0]
                else:
                    # Tool not in any neighborhood — put in first one
                    target_nbr_id = next(iter(nbr_map.values()), None)
                    if target_nbr_id is None:
                        continue

            # Update or insert the pinned membership
            con.execute(
                """
                DELETE FROM neighborhood_members
                WHERE tool_id = $1
                """,
                [pin_tool_id],
            )
            con.execute(
                """
                INSERT INTO neighborhood_members (neighborhood_id, tool_id, membership, pinned)
                VALUES ($1, $2, $3, $4)
                """,
                [target_nbr_id, pin_tool_id, 1.0, True],
            )

    return len(results)


# ── Query helpers ───────────────────────────────────────────────────────────


def get_tool_neighborhood(con: duckdb.DuckDBPyConnection, tool_name: str) -> dict | None:
    """Return the neighborhood a tool belongs to, or None."""
    row = con.execute(
        """
        SELECT n.name, n.description, n.neighborhood_id
        FROM neighborhood_members nm
        JOIN neighborhoods n ON n.neighborhood_id = nm.neighborhood_id
        JOIN tools t ON t.tool_id = nm.tool_id
        WHERE lower(t.name) = lower($1)
        LIMIT 1
        """,
        [tool_name],
    ).fetchone()

    if not row:
        return None

    nbr_name, nbr_desc, nbr_id = row

    # Get other members
    members = con.execute(
        """
        SELECT t.name
        FROM neighborhood_members nm
        JOIN tools t ON t.tool_id = nm.tool_id
        WHERE nm.neighborhood_id = $1
        ORDER BY t.name
        """,
        [nbr_id],
    ).fetchall()

    return {
        "neighborhood": nbr_name,
        "description": nbr_desc,
        "members": [m[0] for m in members],
    }


def get_neighborhood_tools(con: duckdb.DuckDBPyConnection, neighborhood_name: str) -> list[dict]:
    """Return all tools in a neighborhood."""
    rows = con.execute(
        """
        SELECT t.tool_id, t.name, t.summary, t.categories, nm.membership, nm.pinned
        FROM neighborhood_members nm
        JOIN tools t ON t.tool_id = nm.tool_id
        JOIN neighborhoods n ON n.neighborhood_id = nm.neighborhood_id
        WHERE lower(n.name) = lower($1)
        ORDER BY t.name
        """,
        [neighborhood_name],
    ).fetchall()
    cols = [desc[0] for desc in con.description]
    return [dict(zip(cols, row)) for row in rows]
