"""Tests for recommendation engine."""

from __future__ import annotations

import json

import duckdb
import pytest

from landscape.analysis.recommend import (
    Recommendation,
    recommend_for_capability,
    recommend_for_tool,
    recommend_stack,
)
from landscape.db.schema import create_schema


@pytest.fixture
def con():
    """In-memory DuckDB with tools, edges, neighborhoods, and fitness data."""
    db = duckdb.connect(":memory:")
    create_schema(db)

    db.execute(
        """
        INSERT INTO tools (name, categories, capability_ceiling, community_momentum,
            hpc_compatible, open_source) VALUES
            ('ToolA', ['orchestrator', 'ml_framework'], 'extensive', 'growing', 'native', true),
            ('ToolB', ['orchestrator'], 'high', 'stable', 'adaptable', true),
            ('ToolC', ['monitoring'], 'medium', 'stable', 'cloud_only', true),
            ('ToolD', ['orchestrator', 'monitoring'], 'high', 'growing', 'native', true)
        """
    )

    ids = {}
    for row in db.execute("SELECT tool_id, name FROM tools").fetchall():
        ids[row[1]] = row[0]

    # Edges: A requires B, A integrates C
    db.execute(
        "INSERT INTO edges (source_id, target_id, relation, source_info) VALUES "
        f"({ids['ToolA']}, {ids['ToolB']}, 'requires', 'hand_curated'), "
        f"({ids['ToolA']}, {ids['ToolC']}, 'integrates_with', 'hand_curated')"
    )

    # Neighborhood
    db.execute(
        """
        INSERT INTO neighborhoods (name, description, origin, algorithm)
        VALUES ('orchestrators', 'Orchestration tools', 'computed', 'louvain_v1')
        """
    )
    nbr_id = db.execute(
        "SELECT neighborhood_id FROM neighborhoods WHERE name = 'orchestrators'"
    ).fetchone()[0]
    db.execute(
        f"""
        INSERT INTO neighborhood_members (neighborhood_id, tool_id, membership) VALUES
            ({nbr_id}, {ids["ToolA"]}, 1.0),
            ({nbr_id}, {ids["ToolB"]}, 1.0),
            ({nbr_id}, {ids["ToolD"]}, 1.0)
        """
    )

    # Project + capability + fitness
    db.execute("INSERT INTO projects (name, description) VALUES ('TestProj', 'Test')")
    proj_id = db.execute("SELECT project_id FROM projects WHERE name = 'TestProj'").fetchone()[0]
    db.execute(
        """
        INSERT INTO capabilities (project_id, name, description, current_tool_id,
            ceiling_requirements)
        VALUES ($1, 'orchestration', 'Pipeline orchestration', $2, $3)
        """,
        [proj_id, ids["ToolA"], json.dumps({})],
    )
    cap_id = db.execute(
        "SELECT capability_id FROM capabilities WHERE name = 'orchestration'"
    ).fetchone()[0]

    # Fitness scores
    for tool_name, score in [("ToolB", 85.0), ("ToolC", 60.0), ("ToolD", 78.0)]:
        db.execute(
            "INSERT INTO fitness (tool_id, capability_id, overall_fitness, method) "
            f"VALUES ({ids[tool_name]}, {cap_id}, {score}, 'algorithm_v1')"
        )

    yield db
    db.close()


class TestRecommendForTool:
    def test_returns_recommendations(self, con):
        recs = recommend_for_tool(con, "ToolA")
        assert len(recs) > 0
        assert all(isinstance(r, Recommendation) for r in recs)

    def test_direct_edges_ranked_higher(self, con):
        recs = recommend_for_tool(con, "ToolA")
        # ToolB has 'requires' edge = 95, ToolC has 'integrates_with' = 50
        names = [r.tool_name for r in recs]
        if "ToolB" in names and "ToolC" in names:
            assert names.index("ToolB") < names.index("ToolC")

    def test_sorted_by_score(self, con):
        recs = recommend_for_tool(con, "ToolA")
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_tool_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            recommend_for_tool(con, "NonExistent")

    def test_top_n_limits(self, con):
        recs = recommend_for_tool(con, "ToolA", top_n=1)
        assert len(recs) <= 1

    def test_neighborhood_members_included(self, con):
        recs = recommend_for_tool(con, "ToolA")
        names = [r.tool_name for r in recs]
        # ToolD is in same neighborhood but no direct edge
        assert "ToolD" in names


class TestRecommendForCapability:
    def test_returns_fitness_based(self, con):
        recs = recommend_for_capability(con, "TestProj", "orchestration")
        assert len(recs) > 0

    def test_excludes_current_tool(self, con):
        recs = recommend_for_capability(con, "TestProj", "orchestration")
        names = [r.tool_name for r in recs]
        assert "ToolA" not in names  # current tool excluded

    def test_sorted_by_fitness(self, con):
        recs = recommend_for_capability(con, "TestProj", "orchestration")
        scores = [r.score for r in recs]
        assert scores == sorted(scores, reverse=True)

    def test_capability_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            recommend_for_capability(con, "TestProj", "nonexistent")


class TestRecommendStack:
    def test_returns_dict(self, con):
        result = recommend_stack(con, "TestProj")
        assert isinstance(result, dict)
        assert "orchestration" in result

    def test_project_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            recommend_stack(con, "NonExistent")
