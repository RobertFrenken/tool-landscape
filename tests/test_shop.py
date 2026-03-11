"""Tests for the shopping/matching engine."""

from __future__ import annotations

import duckdb
import pytest

from landscape.analysis.shop import (
    MatchReport,
    ScoredCandidate,
    build_filter_query,
    print_shop_report,
    score_candidates,
    shop,
)
from landscape.db.schema import create_schema
from landscape.models.spec import ComponentSpec, ProjectSpec


@pytest.fixture
def test_db():
    """In-memory DuckDB with schema and test tools."""
    con = duckdb.connect(":memory:")
    create_schema(con)

    # Insert test tools
    tools = [
        (
            "MLflow",
            True,
            True,
            "growth",
            "native",
            "shared_server",
            "growing",
            "high",
            "excellent",
            "minimal",
            "low",
            "low",
            ["experiment_tracking"],
            ["python"],
        ),
        (
            "W&B",
            True,
            False,
            "production",
            "adaptable",
            "multi_tenant",
            "growing",
            "extensive",
            "excellent",
            "minimal",
            "medium",
            "medium",
            ["experiment_tracking"],
            ["python"],
        ),
        (
            "ClearML",
            True,
            True,
            "growth",
            "adaptable",
            "shared_server",
            "growing",
            "extensive",
            "adequate",
            "moderate",
            "low",
            "low",
            ["experiment_tracking"],
            ["python"],
        ),
        (
            "TensorFlow",
            True,
            True,
            "production",
            "adaptable",
            "single_user",
            "stable",
            "extensive",
            "excellent",
            "heavy",
            "low",
            "medium",
            ["deep_learning", "machine_learning"],
            ["python"],
        ),
        (
            "PyTorch",
            True,
            True,
            "production",
            "native",
            "single_user",
            "growing",
            "extensive",
            "excellent",
            "moderate",
            "low",
            "low",
            ["deep_learning", "machine_learning"],
            ["python"],
        ),
        (
            "React",
            True,
            False,
            "production",
            "cloud_only",
            "single_user",
            "stable",
            "extensive",
            "excellent",
            "minimal",
            "medium",
            "low",
            ["frontend", "ui_framework"],
            ["javascript"],
        ),
    ]

    for (
        name,
        open_src,
        offline,
        maturity,
        hpc,
        collab,
        momentum,
        ceiling,
        docs,
        overhead,
        lock_in,
        migration,
        categories,
        languages,
    ) in tools:
        con.execute(
            """
            INSERT INTO tools (name, open_source, offline_capable, maturity,
                hpc_compatible, collaboration_model, community_momentum,
                capability_ceiling, documentation_quality, resource_overhead,
                lock_in_risk, migration_cost, categories, language_ecosystem,
                python_native)
            VALUES (?, ?, ?, ?::maturity_level, ?::hpc_compat, ?::collab_model,
                ?::momentum, ?::tier, ?::doc_quality, ?::overhead,
                ?::cost_level, ?::cost_level, ?, ?, ?)
            """,
            [
                name,
                open_src,
                offline,
                maturity,
                hpc,
                collab,
                momentum,
                ceiling,
                docs,
                overhead,
                lock_in,
                migration,
                categories,
                languages,
                "python" in languages,
            ],
        )

    # Insert an edge: MLflow integrates_with PyTorch
    mlflow_id = con.execute("SELECT tool_id FROM tools WHERE name = 'MLflow'").fetchone()[0]
    pytorch_id = con.execute("SELECT tool_id FROM tools WHERE name = 'PyTorch'").fetchone()[0]
    con.execute(
        "INSERT INTO edges (source_id, target_id, relation, weight) VALUES (?, ?, 'integrates_with', 1.0)",
        [mlflow_id, pytorch_id],
    )

    yield con
    con.close()


class TestBuildFilterQuery:
    def test_boolean_filter(self):
        comp = ComponentSpec(require={"offline_capable": True})
        sql, params, labels = build_filter_query(comp)
        assert "offline_capable = $1" in sql
        assert params == [True]
        assert "offline_capable = True" in labels[0]

    def test_enum_filter_include(self):
        comp = ComponentSpec(require={"hpc_compatible": ["native", "adaptable"]})
        sql, params, labels = build_filter_query(comp)
        assert "CAST(hpc_compatible AS VARCHAR) IN" in sql
        assert "native" in params
        assert "adaptable" in params

    def test_enum_filter_exclude(self):
        comp = ComponentSpec(require={"hpc_compatible": ["!cloud_only"]})
        sql, params, labels = build_filter_query(comp)
        assert "NOT IN" in sql
        assert "cloud_only" in params

    def test_array_filter_include(self):
        comp = ComponentSpec(require={"categories": ["experiment_tracking"]})
        sql, params, labels = build_filter_query(comp)
        assert "list_contains(categories" in sql
        assert "experiment_tracking" in params

    def test_array_filter_exclude(self):
        comp = ComponentSpec(require={"categories": ["!gamedev"]})
        sql, params, labels = build_filter_query(comp)
        assert "NOT list_contains" in sql
        assert "gamedev" in params

    def test_no_constraints(self):
        comp = ComponentSpec()
        sql, params, labels = build_filter_query(comp)
        assert "1=1" in sql
        assert params == []
        assert labels == []

    def test_multiple_constraints(self):
        comp = ComponentSpec(
            require={
                "offline_capable": True,
                "open_source": True,
                "hpc_compatible": ["native"],
                "categories": ["experiment_tracking"],
            }
        )
        sql, params, labels = build_filter_query(comp)
        assert "offline_capable" in sql
        assert "open_source" in sql
        assert "hpc_compatible" in sql
        assert "categories" in sql
        assert len(params) == 4

    def test_filter_executes(self, test_db):
        """Verify filter query actually runs against DuckDB."""
        comp = ComponentSpec(
            require={
                "offline_capable": True,
                "categories": ["experiment_tracking"],
            }
        )
        sql, params, _ = build_filter_query(comp)
        rows = test_db.execute(sql, params).fetchall()
        names = {row[1] for row in rows}  # name is column 1
        assert "MLflow" in names
        assert "ClearML" in names
        assert "W&B" not in names  # not offline_capable
        assert "React" not in names  # wrong category


class TestScoreCandidates:
    def test_scores_are_bounded(self, test_db):
        comp = ComponentSpec(
            require={"categories": ["experiment_tracking"]},
            prefer={"capability_ceiling": {"value": "extensive", "weight": 5}},
        )
        sql, params, _ = build_filter_query(comp)
        rows = test_db.execute(sql, params).fetchall()
        cols = [d[0] for d in test_db.description]
        candidates = [dict(zip(cols, r)) for r in rows]

        scored = score_candidates(candidates, comp, test_db, [])
        for s in scored:
            assert 0 <= s.combined_score <= 100
            assert 0 <= s.fitness_score <= 100
            assert 0 <= s.preference_score <= 100
            assert 0 <= s.coherence_score <= 100

    def test_current_tool_marked(self, test_db):
        comp = ComponentSpec(
            current_tool="MLflow",
            require={"categories": ["experiment_tracking"]},
        )
        sql, params, _ = build_filter_query(comp)
        rows = test_db.execute(sql, params).fetchall()
        cols = [d[0] for d in test_db.description]
        candidates = [dict(zip(cols, r)) for r in rows]

        scored = score_candidates(candidates, comp, test_db, [])
        mlflow = [s for s in scored if s.tool_name == "MLflow"]
        assert len(mlflow) == 1
        assert mlflow[0].is_current is True

        others = [s for s in scored if s.tool_name != "MLflow"]
        for s in others:
            assert s.is_current is False

    def test_coherence_with_stack_pins(self, test_db):
        comp = ComponentSpec(
            require={"categories": ["experiment_tracking"]},
        )
        sql, params, _ = build_filter_query(comp)
        rows = test_db.execute(sql, params).fetchall()
        cols = [d[0] for d in test_db.description]
        candidates = [dict(zip(cols, r)) for r in rows]

        # With PyTorch pinned, MLflow should get coherence bonus
        scored = score_candidates(candidates, comp, test_db, ["PyTorch"])
        mlflow = [s for s in scored if s.tool_name == "MLflow"][0]
        assert mlflow.coherence_score > 0

    def test_no_coherence_without_pins(self, test_db):
        comp = ComponentSpec(require={"categories": ["experiment_tracking"]})
        sql, params, _ = build_filter_query(comp)
        rows = test_db.execute(sql, params).fetchall()
        cols = [d[0] for d in test_db.description]
        candidates = [dict(zip(cols, r)) for r in rows]

        scored = score_candidates(candidates, comp, test_db, [])
        for s in scored:
            assert s.coherence_score == 0.0


class TestShop:
    def test_shop_single_component(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "tracking": {
                        "require": {"categories": ["experiment_tracking"]},
                    }
                },
            }
        )
        reports = shop(test_db, spec, component_name="tracking", top_n=5)
        assert "tracking" in reports
        report = reports["tracking"]
        assert isinstance(report, MatchReport)
        assert report.total_tools == 6
        assert len(report.scored_tools) <= 5

    def test_shop_all_components(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "tracking": {"require": {"categories": ["experiment_tracking"]}},
                    "framework": {"require": {"categories": ["deep_learning"]}},
                },
            }
        )
        reports = shop(test_db, spec, top_n=3)
        assert len(reports) == 2
        assert "tracking" in reports
        assert "framework" in reports

    def test_shop_nonexistent_component_raises(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {"tracking": {}},
            }
        )
        with pytest.raises(ValueError, match="not found"):
            shop(test_db, spec, component_name="nonexistent")

    def test_shop_empty_result(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "impossible": {
                        "require": {"categories": ["nonexistent_category"]},
                    }
                },
            }
        )
        reports = shop(test_db, spec, top_n=5)
        assert reports["impossible"].scored_tools == []

    def test_shop_with_notes(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "tracking": {
                        "require": {"categories": ["experiment_tracking"]},
                        "notes": ["needs query API"],
                    }
                },
            }
        )
        reports = shop(test_db, spec, top_n=5)
        assert any("needs query API" in n for n in reports["tracking"].unmatched_notes)


class TestPrintShopReport:
    def test_print_report_runs(self, capsys):
        """Just verify it doesn't crash."""
        reports = {
            "tracking": MatchReport(
                component_name="tracking",
                total_tools=100,
                filter_funnel=[("all tools", 100), ("offline_capable = True", 20)],
                scored_tools=[
                    ScoredCandidate("MLflow", 1, 82.3, 78.1, 88.0, 80.0, True),
                    ScoredCandidate("W&B", 2, 79.1, 81.2, 76.0, 70.0, False),
                ],
                unmatched_notes=["needs query API"],
                coherence_hits=1,
            ),
        }
        print_shop_report(reports)
        output = capsys.readouterr().out
        assert "tracking" in output
        assert "MLflow" in output
        assert "W&B" in output
        assert "query API" in output
