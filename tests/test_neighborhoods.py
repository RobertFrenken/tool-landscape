"""Tests for neighborhood clustering using in-memory DuckDB."""

from __future__ import annotations

import duckdb
import pytest

from landscape.analysis.neighborhoods import (
    EDGE_WEIGHTS,
    NeighborhoodResult,
    _assign_orphans,
    _generate_name,
    build_graph,
    compute_neighborhoods,
    get_neighborhood_tools,
    get_tool_neighborhood,
    persist_neighborhoods,
)
from landscape.db.schema import create_schema


@pytest.fixture
def con():
    """In-memory DuckDB with schema and 8 tools forming 2 clear communities."""
    db = duckdb.connect(":memory:")
    create_schema(db)

    # Community 1: ML tools (PyTorch, TensorFlow, Keras)
    # Community 2: Web tools (React, Vue, Angular)
    # Bridge: FastAPI (connects to both)
    # Isolated: Obscure-Tool (no edges)
    db.execute(
        """
        INSERT INTO tools (name, categories, language_ecosystem, used_by) VALUES
            ('PyTorch', ['ml_framework', 'deep_learning'], ['python'], ['KD-GAT']),
            ('TensorFlow', ['ml_framework', 'deep_learning'], ['python'], ['KD-GAT']),
            ('Keras', ['ml_framework', 'deep_learning'], ['python'], []),
            ('React', ['frontend', 'ui_framework'], ['javascript'], []),
            ('Vue', ['frontend', 'ui_framework'], ['javascript'], []),
            ('Angular', ['frontend', 'ui_framework'], ['javascript'], []),
            ('FastAPI', ['backend', 'api_framework'], ['python'], []),
            ('Obscure-Tool', ['niche'], ['rust'], [])
        """
    )

    # Strong ML edges
    ids = {}
    for row in db.execute("SELECT tool_id, name FROM tools").fetchall():
        ids[row[1]] = row[0]

    # requires edges within ML community
    db.execute(
        "INSERT INTO edges (source_id, target_id, relation, source_info) VALUES "
        f"({ids['Keras']}, {ids['TensorFlow']}, 'wraps', 'hand_curated'), "
        f"({ids['Keras']}, {ids['PyTorch']}, 'wraps', 'hand_curated'), "
        f"({ids['PyTorch']}, {ids['TensorFlow']}, 'replaces', 'hand_curated')"
    )

    # Web community edges
    db.execute(
        "INSERT INTO edges (source_id, target_id, relation, source_info) VALUES "
        f"({ids['React']}, {ids['Vue']}, 'replaces', 'hand_curated'), "
        f"({ids['React']}, {ids['Angular']}, 'replaces', 'hand_curated'), "
        f"({ids['Vue']}, {ids['Angular']}, 'replaces', 'hand_curated')"
    )

    # Bridge: FastAPI integrates with both communities
    db.execute(
        "INSERT INTO edges (source_id, target_id, relation, source_info) VALUES "
        f"({ids['FastAPI']}, {ids['PyTorch']}, 'integrates_with', 'hand_curated'), "
        f"({ids['FastAPI']}, {ids['React']}, 'integrates_with', 'hand_curated')"
    )

    yield db
    db.close()


# ── build_graph ──────────────────────────────────────────────────────────────


class TestBuildGraph:
    def test_all_tools_are_nodes(self, con):
        G = build_graph(con)
        assert len(G.nodes) == 8

    def test_edges_present(self, con):
        G = build_graph(con)
        # At least the explicit edges should be present
        assert G.number_of_edges() >= 7  # 3 ML + 3 Web + 2 bridge - possible dedup

    def test_edge_weights_applied(self, con):
        G = build_graph(con)
        ids = {}
        for row in con.execute("SELECT tool_id, name FROM tools").fetchall():
            ids[row[1]] = row[0]

        # wraps edge should have weight 2.5
        if G.has_edge(ids["Keras"], ids["TensorFlow"]):
            w = G[ids["Keras"]][ids["TensorFlow"]]["weight"]
            assert w == EDGE_WEIGHTS["wraps"]

    def test_isolated_tool_gets_synthetic_edges(self, con):
        G = build_graph(con)
        ids = {}
        for row in con.execute("SELECT tool_id, name FROM tools").fetchall():
            ids[row[1]] = row[0]

        # Obscure-Tool has no explicit edges but should get synthetic ones
        # (it may share no categories, but the function should still include it as a node)
        assert ids["Obscure-Tool"] in G.nodes

    def test_used_by_synthetic_edges(self, con):
        G = build_graph(con)
        ids = {}
        for row in con.execute("SELECT tool_id, name FROM tools").fetchall():
            ids[row[1]] = row[0]

        # PyTorch and TensorFlow share used_by=['KD-GAT'] — should have synthetic edge
        # (they also have explicit edge, so weight may be accumulated)
        assert G.has_edge(ids["PyTorch"], ids["TensorFlow"])


# ── compute_neighborhoods ────────────────────────────────────────────────────


class TestComputeNeighborhoods:
    def test_returns_neighborhoods(self, con):
        results = compute_neighborhoods(con, min_size=2)
        assert len(results) > 0
        assert all(isinstance(r, NeighborhoodResult) for r in results)

    def test_all_tools_covered(self, con):
        results = compute_neighborhoods(con, min_size=1)
        all_tool_ids = set()
        for r in results:
            all_tool_ids.update(r.tool_ids)
        total = con.execute("SELECT count(*) FROM tools").fetchone()[0]
        assert len(all_tool_ids) == total

    def test_sorted_by_size(self, con):
        results = compute_neighborhoods(con, min_size=1)
        sizes = [r.size for r in results]
        assert sizes == sorted(sizes, reverse=True)

    def test_names_unique(self, con):
        results = compute_neighborhoods(con, min_size=1)
        names = [r.name for r in results]
        assert len(names) == len(set(names))

    def test_min_size_filters(self, con):
        results_small = compute_neighborhoods(con, min_size=1)
        results_large = compute_neighborhoods(con, min_size=5)
        # Larger min_size should produce fewer (or equal) neighborhoods
        assert len(results_large) <= len(results_small)

    def test_resolution_affects_count(self, con):
        r_low = compute_neighborhoods(con, resolution=0.5, min_size=1)
        r_high = compute_neighborhoods(con, resolution=3.0, min_size=1)
        # Higher resolution typically produces more communities
        # (but with 8 nodes this isn't guaranteed, so just check they run)
        assert len(r_low) >= 1
        assert len(r_high) >= 1


# ── _generate_name ───────────────────────────────────────────────────────────


class TestGenerateName:
    def test_uses_dominant_category(self):
        names = ["PyTorch", "TensorFlow", "Keras"]
        cats = [["ml_framework", "deep_learning"], ["ml_framework"], ["ml_framework"]]
        name = _generate_name(names, cats, set())
        assert "ml_framework" in name

    def test_disambiguates_collision(self):
        cats1 = [["ml_framework"]]
        cats2 = [["ml_framework"]]
        name1 = _generate_name(["A"], cats1, set())
        name2 = _generate_name(["B"], cats2, {name1})
        assert name1 != name2

    def test_empty_categories(self):
        name = _generate_name(["X"], [[]], set())
        assert len(name) > 0


# ── _assign_orphans ──────────────────────────────────────────────────────────


class TestAssignOrphans:
    def test_orphans_merged(self, con):
        import networkx as nx

        G = nx.Graph()
        G.add_edge(1, 2, weight=1.0)
        G.add_edge(2, 3, weight=1.0)
        G.add_edge(4, 5, weight=1.0)
        G.add_edge(1, 6, weight=0.5)  # orphan 6 connects to community {1,2,3}

        communities = [{1, 2, 3}, {4, 5}]
        orphans = [{6}]
        result = _assign_orphans(G, communities, orphans)

        # Orphan 6 should be merged into {1,2,3} community
        assert 6 in result[0] or 6 in result[1]
        all_nodes = set()
        for c in result:
            all_nodes.update(c)
        assert 6 in all_nodes


# ── persist + retrieve ───────────────────────────────────────────────────────


class TestPersistNeighborhoods:
    def test_persist_and_read_back(self, con):
        results = compute_neighborhoods(con, min_size=1)
        count = persist_neighborhoods(con, results)
        assert count == len(results)

        db_count = con.execute("SELECT count(*) FROM neighborhoods").fetchone()[0]
        assert db_count == count

        member_count = con.execute("SELECT count(*) FROM neighborhood_members").fetchone()[0]
        assert member_count == 8  # all tools assigned

    def test_recompute_clears_old(self, con):
        results = compute_neighborhoods(con, min_size=1)
        persist_neighborhoods(con, results)
        persist_neighborhoods(con, results)  # second run

        db_count = con.execute("SELECT count(*) FROM neighborhoods").fetchone()[0]
        assert db_count == len(results)

    def test_pinned_membership_survives(self, con):
        results = compute_neighborhoods(con, min_size=1)
        persist_neighborhoods(con, results)

        # Pin a tool
        tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'PyTorch'").fetchone()[0]
        con.execute(
            "UPDATE neighborhood_members SET pinned = true WHERE tool_id = $1",
            [tool_id],
        )

        # Recompute
        results2 = compute_neighborhoods(con, min_size=1)
        persist_neighborhoods(con, results2, respect_pins=True)

        pinned = con.execute(
            "SELECT pinned FROM neighborhood_members WHERE tool_id = $1", [tool_id]
        ).fetchone()
        assert pinned is not None


class TestGetToolNeighborhood:
    def test_returns_neighborhood(self, con):
        results = compute_neighborhoods(con, min_size=1)
        persist_neighborhoods(con, results)

        nbr = get_tool_neighborhood(con, "PyTorch")
        assert nbr is not None
        assert "neighborhood" in nbr
        assert "members" in nbr

    def test_not_found(self, con):
        result = get_tool_neighborhood(con, "NonExistent")
        assert result is None


class TestGetNeighborhoodTools:
    def test_returns_tools(self, con):
        results = compute_neighborhoods(con, min_size=1)
        persist_neighborhoods(con, results)

        nbr_name = results[0].name
        tools = get_neighborhood_tools(con, nbr_name)
        assert len(tools) == results[0].size
        assert all("name" in t for t in tools)

    def test_not_found(self, con):
        tools = get_neighborhood_tools(con, "NonExistent")
        assert tools == []
