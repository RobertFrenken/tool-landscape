"""Tests for fitness scoring algorithm using in-memory DuckDB."""

from __future__ import annotations

import json

import duckdb
import pytest

from landscape.analysis.fitness import (
    DEFAULT_WEIGHTS,
    ScoredTool,
    _log_normalize,
    _recency_score,
    compute_requirement_coverage,
    get_latest_metrics,
    persist_scores,
    score_single_tool,
    score_tool_capability,
)
from landscape.db.schema import create_schema


@pytest.fixture
def con():
    """In-memory DuckDB with schema and seed data."""
    db = duckdb.connect(":memory:")
    create_schema(db)

    # Insert test tools
    db.execute(
        """
        INSERT INTO tools (name, capability_ceiling, community_momentum,
            lock_in_risk, hpc_compatible, documentation_quality,
            resource_overhead, maturity, offline_capable, collaboration_model,
            open_source, python_native)
        VALUES
            ('ToolA', 'extensive', 'growing', 'low', 'native', 'excellent',
             'minimal', 'production', true, 'multi_tenant', true, true),
            ('ToolB', 'medium', 'stable', 'high', 'cloud_only', 'adequate',
             'heavy', 'growth', false, 'single_user', true, false),
            ('ToolC', 'high', 'declining', 'medium', 'adaptable', 'poor',
             'moderate', 'early', true, 'shared_server', false, true)
        """
    )

    # Insert a project
    db.execute(
        """
        INSERT INTO projects (name, description, team_size_ceiling, env_primary, gpu_required)
        VALUES ('TestProject', 'Test', 5, 'hpc', true)
        """
    )
    project_id = db.execute(
        "SELECT project_id FROM projects WHERE name = 'TestProject'"
    ).fetchone()[0]

    # Insert capabilities with requirements
    db.execute(
        """
        INSERT INTO capabilities (project_id, name, description,
            floor_requirements, ceiling_requirements)
        VALUES
            ($1, 'orchestration', 'Pipeline orchestration',
             '{}',
             $2),
            ($1, 'tracking', 'Experiment tracking',
             '{}',
             $3)
        """,
        [
            project_id,
            json.dumps(
                {
                    "offline_capable": True,
                    "hpc_compatible": ["native", "adaptable"],
                    "collaboration_model": ["shared_server", "multi_tenant"],
                }
            ),
            json.dumps(
                {
                    "offline_capable": True,
                    "hpc_compatible": ["native", "adaptable"],
                }
            ),
        ],
    )

    yield db
    db.close()


@pytest.fixture
def tool_a(con):
    """Get ToolA as a dict."""
    row = con.execute("SELECT * FROM tools WHERE name = 'ToolA'").fetchone()
    cols = [desc[0] for desc in con.description]
    return dict(zip(cols, row))


@pytest.fixture
def tool_b(con):
    """Get ToolB as a dict."""
    row = con.execute("SELECT * FROM tools WHERE name = 'ToolB'").fetchone()
    cols = [desc[0] for desc in con.description]
    return dict(zip(cols, row))


@pytest.fixture
def cap_orchestration(con):
    """Get orchestration capability as a dict."""
    row = con.execute("SELECT * FROM capabilities WHERE name = 'orchestration'").fetchone()
    cols = [desc[0] for desc in con.description]
    return dict(zip(cols, row))


@pytest.fixture
def cap_tracking(con):
    """Get tracking capability as a dict."""
    row = con.execute("SELECT * FROM capabilities WHERE name = 'tracking'").fetchone()
    cols = [desc[0] for desc in con.description]
    return dict(zip(cols, row))


# ── Unit tests for helper functions ──────────────────────────────────────────


class TestLogNormalize:
    def test_zero_value(self):
        assert _log_normalize(0.0, floor=0.0, ceiling=1e8) == 0.0

    def test_at_ceiling(self):
        assert _log_normalize(1e8, floor=0.0, ceiling=1e8) == pytest.approx(1.0)

    def test_below_floor(self):
        assert _log_normalize(50, floor=100, ceiling=1e8) == 0.0

    def test_mid_range(self):
        result = _log_normalize(1e4, floor=100, ceiling=1e8)
        assert 0.0 < result < 1.0

    def test_above_ceiling_clamped(self):
        assert _log_normalize(1e10, floor=0.0, ceiling=1e8) == 1.0


class TestRecencyScore:
    def test_fresh_release(self):
        assert _recency_score(0) == 1.0

    def test_year_old(self):
        assert _recency_score(365) == pytest.approx(0.5, abs=0.01)

    def test_two_years_old(self):
        assert _recency_score(730) == 0.0

    def test_ancient(self):
        assert _recency_score(1000) == 0.0


# ── Requirement coverage ─────────────────────────────────────────────────────


class TestRequirementCoverage:
    def test_empty_requirements(self):
        coverage, reasons = compute_requirement_coverage({}, {})
        assert coverage == 1.0

    def test_offline_met(self):
        tool = {"offline_capable": True}
        reqs = {"offline_capable": True}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 1.0

    def test_offline_not_met(self):
        tool = {"offline_capable": False}
        reqs = {"offline_capable": True}
        coverage, reasons = compute_requirement_coverage(tool, reqs)
        assert coverage == 0.0
        assert any("offline_capable" in r for r in reasons)

    def test_hpc_compatible_list(self):
        tool = {"hpc_compatible": "native"}
        reqs = {"hpc_compatible": ["native", "adaptable"]}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 1.0

    def test_hpc_not_compatible(self):
        tool = {"hpc_compatible": "cloud_only"}
        reqs = {"hpc_compatible": ["native", "adaptable"]}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 0.0

    def test_collab_model_match(self):
        tool = {"collaboration_model": "multi_tenant"}
        reqs = {"collaboration_model": ["shared_server", "multi_tenant"]}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 1.0

    def test_mixed_requirements(self):
        tool = {"offline_capable": True, "hpc_compatible": "native"}
        reqs = {"offline_capable": True, "hpc_compatible": ["native"]}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 1.0

    def test_partial_coverage(self):
        tool = {"offline_capable": False, "hpc_compatible": "native"}
        reqs = {"offline_capable": True, "hpc_compatible": ["native"]}
        coverage, _ = compute_requirement_coverage(tool, reqs)
        assert coverage == 0.5


# ── Score tool-capability pairs ──────────────────────────────────────────────


class TestScoreToolCapability:
    def test_high_quality_tool_scores_well(self, tool_a, cap_orchestration):
        result = score_tool_capability(tool_a, cap_orchestration, {})
        assert isinstance(result, ScoredTool)
        assert result.overall_fitness > 50.0
        assert result.tool_name == "ToolA"
        assert result.capability_name == "orchestration"

    def test_low_quality_tool_scores_poorly(self, tool_b, cap_orchestration):
        result = score_tool_capability(tool_b, cap_orchestration, {})
        # ToolB: cloud_only, high lock-in, heavy overhead — should score lower than ToolA
        assert result.overall_fitness < 50.0

    def test_tool_a_beats_tool_b(self, tool_a, tool_b, cap_orchestration):
        score_a = score_tool_capability(tool_a, cap_orchestration, {})
        score_b = score_tool_capability(tool_b, cap_orchestration, {})
        assert score_a.overall_fitness > score_b.overall_fitness

    def test_metrics_add_components(self, tool_a, cap_orchestration):
        no_metrics = score_tool_capability(tool_a, cap_orchestration, {})
        with_metrics = score_tool_capability(
            tool_a,
            cap_orchestration,
            {
                "pypi_downloads_monthly": 5_000_000,
                "github_stars": 50_000,
                "openssf_score": 8.5,
                "days_since_last_release": 10,
            },
        )
        # Metrics should add more components to the breakdown
        assert len(with_metrics.components) > len(no_metrics.components)
        assert "downloads" in with_metrics.components
        assert "stars" in with_metrics.components
        assert "openssf" in with_metrics.components
        assert "recency" in with_metrics.components
        # Score should still be high (ToolA is excellent + strong metrics)
        assert with_metrics.overall_fitness > 90.0

    def test_scores_in_range(self, tool_a, cap_orchestration):
        result = score_tool_capability(tool_a, cap_orchestration, {})
        assert 0.0 <= result.overall_fitness <= 100.0
        assert 0.0 <= result.floor_coverage <= 100.0
        assert 0.0 <= result.ceiling_coverage <= 100.0

    def test_components_populated(self, tool_a, cap_orchestration):
        result = score_tool_capability(tool_a, cap_orchestration, {})
        assert "ceiling_fit" in result.components
        assert "momentum" in result.components
        assert "lock_in_risk" in result.components

    def test_reasoning_populated(self, tool_a, cap_orchestration):
        result = score_tool_capability(tool_a, cap_orchestration, {})
        assert len(result.reasoning) > 0
        assert "top signals" in result.reasoning

    def test_custom_weights(self, tool_a, cap_orchestration):
        # All weight on momentum
        weights = {k: 0.0 for k in DEFAULT_WEIGHTS}
        weights["momentum"] = 1.0
        result = score_tool_capability(tool_a, cap_orchestration, {}, weights=weights)
        # ToolA has growing momentum = 1.0, so qualitative = 1.0
        # overall = 0.6 * 1.0 + 0.4 * ceiling_coverage
        assert result.overall_fitness > 50.0


# ── Metrics retrieval ────────────────────────────────────────────────────────


class TestGetLatestMetrics:
    def test_no_metrics(self, con):
        tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'ToolA'").fetchone()[0]
        metrics = get_latest_metrics(con, tool_id)
        assert metrics == {}

    def test_with_metrics(self, con):
        tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'ToolA'").fetchone()[0]
        con.execute(
            """
            INSERT INTO tool_metrics (tool_id, metric_name, value, source, measured_at)
            VALUES ($1, 'github_stars', 15000, 'github_api', '2026-03-01')
            """,
            [tool_id],
        )
        metrics = get_latest_metrics(con, tool_id)
        assert metrics["github_stars"] == 15000

    def test_latest_metric_wins(self, con):
        tool_id = con.execute("SELECT tool_id FROM tools WHERE name = 'ToolA'").fetchone()[0]
        con.execute(
            """
            INSERT INTO tool_metrics (tool_id, metric_name, value, source, measured_at)
            VALUES
                ($1, 'github_stars', 10000, 'github_api', '2026-01-01'),
                ($1, 'github_stars', 20000, 'github_api', '2026-03-01')
            """,
            [tool_id],
        )
        metrics = get_latest_metrics(con, tool_id)
        assert metrics["github_stars"] == 20000


# ── Integration: score_single_tool ───────────────────────────────────────────


class TestScoreSingleTool:
    def test_scores_against_all_capabilities(self, con):
        results = score_single_tool(con, "ToolA")
        assert len(results) == 2  # orchestration + tracking
        # Should be sorted by overall_fitness descending
        assert results[0].overall_fitness >= results[1].overall_fitness

    def test_tool_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            score_single_tool(con, "NonExistentTool")


# ── Persist scores ───────────────────────────────────────────────────────────


class TestPersistScores:
    def test_persist_and_read_back(self, con, tool_a, cap_orchestration):
        scored = score_tool_capability(tool_a, cap_orchestration, {})
        count = persist_scores(con, [scored])
        assert count == 1

        row = con.execute(
            "SELECT floor_coverage, ceiling_coverage, overall_fitness, method "
            "FROM fitness WHERE tool_id = $1 AND capability_id = $2",
            [scored.tool_id, scored.capability_id],
        ).fetchone()
        assert row is not None
        assert row[0] == scored.floor_coverage
        assert row[1] == scored.ceiling_coverage
        assert row[2] == scored.overall_fitness
        assert row[3] == "algorithm_v1"

    def test_persist_overwrites(self, con, tool_a, cap_orchestration):
        scored = score_tool_capability(tool_a, cap_orchestration, {})
        persist_scores(con, [scored])
        persist_scores(con, [scored])  # second write should replace

        count = con.execute(
            "SELECT count(*) FROM fitness WHERE tool_id = $1 AND capability_id = $2",
            [scored.tool_id, scored.capability_id],
        ).fetchone()[0]
        assert count == 1
