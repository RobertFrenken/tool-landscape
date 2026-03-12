"""Tests for Phase C engine upgrades: propagate_constraints, migration_roi, shop_stack."""

from __future__ import annotations

import duckdb
import pytest

from landscape.analysis.shop import (
    MatchReport,
    StackScore,
    migration_roi,
    print_shop_report,
    print_stack_scores,
    propagate_constraints,
    shop_stack,
    stack_scores_to_json,
)
from landscape.db.schema import create_schema
from landscape.models.spec import ProjectSpec

# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def test_db():
    """In-memory DuckDB with schema, test tools, and typed edges."""
    con = duckdb.connect(":memory:")
    create_schema(con)

    tools = [
        # (name, open_source, offline, maturity, hpc, collab, momentum, ceiling,
        #  docs, overhead, lock_in, migration, categories, languages)
        (
            "Ray",
            True,
            True,
            "production",
            "native",
            "shared_server",
            "growing",
            "extensive",
            "excellent",
            "heavy",
            "low",
            "low",
            ["orchestration", "distributed_computing"],
            ["python"],
        ),
        (
            "Ray Tune",
            True,
            True,
            "growth",
            "native",
            "shared_server",
            "growing",
            "high",
            "excellent",
            "moderate",
            "low",
            "low",
            ["hyperparameter_tuning", "orchestration"],
            ["python"],
        ),
        (
            "Optuna",
            True,
            True,
            "production",
            "adaptable",
            "single_user",
            "growing",
            "high",
            "excellent",
            "minimal",
            "low",
            "low",
            ["hyperparameter_tuning"],
            ["python"],
        ),
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
            "medium",
            "medium",
            ["deep_learning", "machine_learning"],
            ["python"],
        ),
        (
            "Conda",
            True,
            True,
            "production",
            "native",
            "shared_server",
            "stable",
            "medium",
            "adequate",
            "moderate",
            "medium",
            "medium",
            ["package_management"],
            ["python"],
        ),
        (
            "uv",
            True,
            True,
            "growth",
            "native",
            "single_user",
            "growing",
            "high",
            "excellent",
            "minimal",
            "low",
            "low",
            ["package_management"],
            ["python"],
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

    # Edges
    def tid(name: str) -> int:
        return con.execute("SELECT tool_id FROM tools WHERE name = ?", [name]).fetchone()[0]

    ray_id = tid("Ray")
    ray_tune_id = tid("Ray Tune")
    optuna_id = tid("Optuna")
    mlflow_id = tid("MLflow")
    pytorch_id = tid("PyTorch")
    tf_id = tid("TensorFlow")
    conda_id = tid("Conda")
    uv_id = tid("uv")

    edges = [
        # Ray Tune requires Ray (transitive must-include)
        (ray_tune_id, ray_id, "requires", 3.0),
        # Ray Tune wraps Optuna (wraps must-include its target)
        (ray_tune_id, optuna_id, "wraps", 2.0),
        # uv replaces Conda (selecting uv must exclude Conda)
        (uv_id, conda_id, "replaces", 2.0),
        # MLflow integrates_with PyTorch (coherence edge)
        (mlflow_id, pytorch_id, "integrates_with", 1.0),
        # Ray integrates_with MLflow (coherence)
        (ray_id, mlflow_id, "integrates_with", 1.0),
        # TensorFlow often_paired with Conda
        (tf_id, conda_id, "often_paired", 1.0),
    ]

    for src, tgt, rel, wt in edges:
        con.execute(
            "INSERT INTO edges (source_id, target_id, relation, weight) VALUES (?, ?, ?, ?)",
            [src, tgt, rel, wt],
        )

    yield con
    con.close()


# ── C1: propagate_constraints ─────────────────────────────────────────────────


class TestPropagateConstraints:
    def test_empty_selection_returns_empty(self, test_db):
        must_inc, must_exc = propagate_constraints(test_db, {})
        assert must_inc == set()
        assert must_exc == set()

    def test_requires_direct(self, test_db):
        """Selecting Ray Tune should pull in Ray (requires) and Optuna (wraps)."""
        must_inc, must_exc = propagate_constraints(test_db, {"tuner": "Ray Tune"})
        assert "ray" in must_inc
        assert "optuna" in must_inc

    def test_requires_not_duplicated_in_must_include(self, test_db):
        """Selected tools should not appear in must_include."""
        must_inc, must_exc = propagate_constraints(
            test_db, {"tuner": "Ray Tune", "orchestrator": "Ray"}
        )
        # Ray is already selected — should NOT be in must_include
        assert "ray" not in must_inc

    def test_replaces_exclusion(self, test_db):
        """Selecting uv should exclude Conda."""
        must_inc, must_exc = propagate_constraints(test_db, {"pkg": "uv"})
        assert "conda" in must_exc

    def test_replaces_selected_not_excluded(self, test_db):
        """Conda is not excluded when it IS selected (uv not in selection)."""
        must_inc, must_exc = propagate_constraints(test_db, {"pkg": "Conda"})
        assert "conda" not in must_exc

    def test_no_edges_tool_has_no_deps(self, test_db):
        """PyTorch has no outgoing requires/wraps/replaces edges — nothing propagated."""
        must_inc, must_exc = propagate_constraints(test_db, {"dl": "PyTorch"})
        assert must_inc == set()
        assert must_exc == set()

    def test_unknown_tool_name_ignored(self, test_db):
        """Non-existent tool names produce empty sets."""
        must_inc, must_exc = propagate_constraints(test_db, {"x": "NonExistentTool123"})
        assert must_inc == set()
        assert must_exc == set()

    def test_transitive_wraps(self, test_db):
        """Ray Tune wraps Optuna: selecting Ray Tune requires Optuna transitively."""
        must_inc, must_exc = propagate_constraints(test_db, {"tuner": "Ray Tune"})
        assert "optuna" in must_inc


# ── C3: migration_roi ─────────────────────────────────────────────────────────


class TestMigrationRoi:
    def _make_spec(self, **kwargs) -> ProjectSpec:
        return ProjectSpec.model_validate(kwargs)

    def test_no_migration_returns_empty(self):
        spec = self._make_spec(spec_version="2", components={})
        assert migration_roi(spec) == {}

    def test_strong_migrate(self):
        """Annual friction >> effort → ratio > 5 → signal 1.0."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"experiment_tracking": {"effort_hours": 10}},
                    "ongoing_friction": {"experiment_tracking": {"hours_per_week": 2.0}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = (2.0 * 52) / 10 = 10.4 → 1.0
        assert roi["experiment_tracking"] == 1.0

    def test_moderate_ratio_continuous(self):
        """Moderate ratio produces continuous signal, not a discrete bucket."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"pkg_mgmt": {"effort_hours": 20}},
                    "ongoing_friction": {"pkg_mgmt": {"hours_per_week": 1.0}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = (1.0 * 52) / 20 = 2.6 → min(1.0, 2.6/10) = 0.26
        assert abs(roi["pkg_mgmt"] - 0.26) < 0.001

    def test_low_ratio_continuous(self):
        """Low ratio produces a small but non-zero continuous signal."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"framework": {"effort_hours": 200}},
                    "ongoing_friction": {"framework": {"hours_per_week": 0.5}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = (0.5 * 52) / 200 = 0.13 → min(1.0, 0.13/10) ≈ 0.013
        assert roi["framework"] < 0.05
        assert roi["framework"] >= 0.0

    def test_trend_increasing_boosts_roi(self):
        """Increasing friction trend multiplies ratio by 1.3."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"comp": {"effort_hours": 52}},
                    "ongoing_friction": {"comp": {"hours_per_week": 1.0, "trend": "increasing"}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = (1.0 * 52) / 52 = 1.0, × 1.3 = 1.3 → min(1.0, 1.3/10) = 0.13
        assert abs(roi["comp"] - 0.13) < 0.001

    def test_trend_decreasing_reduces_roi(self):
        """Decreasing friction trend multiplies ratio by 0.7."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"comp": {"effort_hours": 52}},
                    "ongoing_friction": {"comp": {"hours_per_week": 1.0, "trend": "decreasing"}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = (1.0 * 52) / 52 = 1.0, × 0.7 = 0.7 → min(1.0, 0.7/10) = 0.07
        assert abs(roi["comp"] - 0.07) < 0.001

    def test_trend_stable_no_adjustment(self):
        """Stable trend does not adjust ratio."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"comp": {"effort_hours": 52}},
                    "ongoing_friction": {"comp": {"hours_per_week": 1.0, "trend": "stable"}},
                },
            }
        )
        roi = migration_roi(spec)
        # ratio = 1.0 → min(1.0, 1.0/10) = 0.10
        assert abs(roi["comp"] - 0.10) < 0.001

    def test_partial_data_neutral(self):
        """Component with only one_time (no friction) → neutral 0.5."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"framework": {"effort_hours": 100}},
                    "ongoing_friction": {},
                },
            }
        )
        roi = migration_roi(spec)
        assert roi["framework"] == 0.5

    def test_zero_effort_is_strong_migrate(self):
        """Zero migration effort → free migration → strong migrate → 1.0."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {"tool": {"effort_hours": 0}},
                    "ongoing_friction": {"tool": {"hours_per_week": 1.0}},
                },
            }
        )
        roi = migration_roi(spec)
        assert roi["tool"] == 1.0

    def test_multiple_components(self):
        """Different components produce different ROI signals (continuous)."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "migration": {
                    "one_time": {
                        "a": {"effort_hours": 10},  # ratio 10.4 → min(1.0, 1.04) = 1.0
                        "b": {"effort_hours": 200},  # ratio 0.13 → min(1.0, 0.013) ≈ 0.013
                    },
                    "ongoing_friction": {
                        "a": {"hours_per_week": 2.0},
                        "b": {"hours_per_week": 0.5},
                    },
                },
            }
        )
        roi = migration_roi(spec)
        assert roi["a"] == 1.0
        assert roi["b"] < 0.05  # very low, but not exactly 0.0
        assert roi["a"] > roi["b"]  # a clearly better than b


# ── C4: shop_stack ────────────────────────────────────────────────────────────


class TestShopStack:
    def _base_spec(self, candidate_stacks: dict, **extra) -> ProjectSpec:
        data: dict = {
            "spec_version": "2",
            "components": {
                "framework": {
                    "description": "ML framework",
                    "current_tool": "PyTorch",
                    "require": {"categories": ["deep_learning"]},
                },
                "tracking": {
                    "description": "Experiment tracking",
                    "current_tool": "MLflow",
                    "require": {"categories": ["experiment_tracking"]},
                },
            },
            "candidate_stacks": candidate_stacks,
        }
        data.update(extra)
        return ProjectSpec.model_validate(data)

    def test_empty_candidate_stacks_returns_empty(self, test_db):
        spec = self._base_spec({})
        result = shop_stack(test_db, spec)
        assert result == {}

    def test_returns_stack_score_per_candidate(self, test_db):
        spec = self._base_spec(
            {
                "pytorch-mlflow": {"framework": "PyTorch", "tracking": "MLflow"},
                "tf-mlflow": {"framework": "TensorFlow", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        assert "pytorch-mlflow" in result
        assert "tf-mlflow" in result
        assert all(isinstance(v, StackScore) for v in result.values())

    def test_scores_are_bounded(self, test_db):
        spec = self._base_spec(
            {
                "stack-a": {"framework": "PyTorch", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        ss = result["stack-a"]
        assert 0.0 <= ss.total_score <= 1.0
        assert 0.0 <= ss.avg_fitness <= 1.0
        assert 0.0 <= ss.internal_coherence <= 1.0
        assert 0.0 <= ss.boundary_friction <= 1.0
        assert 0.0 <= ss.migration_roi <= 1.0
        assert 0.0 <= ss.time_horizon_fit <= 1.0

    def test_unknown_tool_gets_zero_fitness(self, test_db):
        spec = self._base_spec(
            {
                "stack-unknown": {"framework": "NonExistentTool999", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        ss = result["stack-unknown"]
        assert ss.per_tool_fitness["framework"] == 0.0
        assert any("NonExistentTool999" in n for n in ss.notes)

    def test_sorted_by_total_score_descending(self, test_db):
        spec = self._base_spec(
            {
                "stack-a": {"framework": "PyTorch", "tracking": "MLflow"},
                "stack-b": {"framework": "TensorFlow", "tracking": "MLflow"},
                "stack-c": {"framework": "NonExistent", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        scores = [ss.total_score for ss in result.values()]
        assert scores == sorted(scores, reverse=True)

    def test_constraint_violations_detected(self, test_db):
        """Stack with uv + Conda: uv replaces Conda → violation."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "candidate_stacks": {
                    "conflict-stack": {"pkg_a": "uv", "pkg_b": "Conda"},
                },
            }
        )
        result = shop_stack(test_db, spec)
        ss = result["conflict-stack"]
        assert len(ss.constraint_violations) > 0
        assert any("conda" in v.lower() for v in ss.constraint_violations)

    def test_requires_violation_detected(self, test_db):
        """Stack with Ray Tune but not Ray: missing required dependency."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "candidate_stacks": {
                    "missing-dep": {"tuner": "Ray Tune"},
                },
            }
        )
        result = shop_stack(test_db, spec)
        ss = result["missing-dep"]
        # Ray and Optuna are required by Ray Tune but not in stack → violations
        assert len(ss.constraint_violations) > 0

    def test_current_stack_roi_is_neutral(self, test_db):
        """Stack matching all current_tools should have roi = 0.5."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {
                    "framework": {"current_tool": "PyTorch"},
                    "tracking": {"current_tool": "MLflow"},
                },
                "migration": {
                    "one_time": {
                        "framework": {"effort_hours": 10},
                        "tracking": {"effort_hours": 10},
                    },
                    "ongoing_friction": {
                        "framework": {"hours_per_week": 5.0},
                        "tracking": {"hours_per_week": 5.0},
                    },
                },
                "candidate_stacks": {
                    "current": {"framework": "PyTorch", "tracking": "MLflow"},
                },
            }
        )
        result = shop_stack(test_db, spec)
        assert result["current"].migration_roi == 0.5

    def test_none_tool_gets_zero_fitness_and_note(self, test_db):
        """A None slot should score 0 fitness and add a note."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "candidate_stacks": {
                    "partial": {"framework": "PyTorch", "tracking": None},
                },
            }
        )
        result = shop_stack(test_db, spec)
        ss = result["partial"]
        assert ss.per_tool_fitness["tracking"] == 0.0
        assert any("no tool specified" in n for n in ss.notes)

    def test_coherent_stack_has_higher_coherence(self, test_db):
        """MLflow + PyTorch (integrates_with edge) should have higher coherence than unrelated."""
        spec_coherent = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "candidate_stacks": {
                    "coherent": {"tracking": "MLflow", "dl": "PyTorch"},
                },
            }
        )
        spec_unrelated = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "candidate_stacks": {
                    "unrelated": {"tracking": "MLflow", "pkg": "uv"},
                },
            }
        )
        r_coherent = shop_stack(test_db, spec_coherent)
        r_unrelated = shop_stack(test_db, spec_unrelated)
        assert (
            r_coherent["coherent"].internal_coherence > r_unrelated["unrelated"].internal_coherence
        )

    def test_json_output_is_valid(self, test_db):
        """stack_scores_to_json should produce parseable JSON with all keys."""
        import json

        spec = self._base_spec(
            {
                "stack-a": {"framework": "PyTorch", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        raw = stack_scores_to_json(result)
        parsed = json.loads(raw)
        assert "stack-a" in parsed
        entry = parsed["stack-a"]
        for key in [
            "stack_name",
            "tools",
            "per_tool_fitness",
            "avg_fitness",
            "internal_coherence",
            "boundary_friction",
            "migration_roi",
            "time_horizon_fit",
            "total_score",
            "constraint_violations",
            "notes",
        ]:
            assert key in entry, f"Missing key: {key}"

    def test_print_stack_scores_runs(self, test_db, capsys):
        """print_stack_scores should not crash and include stack names."""
        spec = self._base_spec(
            {
                "stack-a": {"framework": "PyTorch", "tracking": "MLflow"},
                "stack-b": {"framework": "TensorFlow", "tracking": "MLflow"},
            }
        )
        result = shop_stack(test_db, spec)
        print_stack_scores(result)
        output = capsys.readouterr().out
        assert "stack-a" in output or "stack-b" in output
        assert "WINNER" in output

    def test_print_stack_scores_empty(self, capsys):
        """print_stack_scores with empty dict should not crash."""
        print_stack_scores({})
        output = capsys.readouterr().out
        assert "No candidate stacks" in output

    def test_boundary_override_lowers_friction_for_challenger(self, test_db):
        """stack_boundary_overrides reduces friction score for the named stack only."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "data_flow": {
                    "stages": [
                        {"name": "serving"},
                        {"name": "presentation"},
                    ],
                    "boundaries": [
                        {"between": ["serving", "presentation"], "friction": "high"},
                    ],
                },
                "candidate_stacks": {
                    "current": {"framework": "PyTorch"},
                    "challenger": {"framework": "TensorFlow"},
                },
                "stack_boundary_overrides": {
                    "challenger": [
                        {
                            "between": ["serving", "presentation"],
                            "friction": "medium",
                            "notes": "Challenger has lower impedance",
                        }
                    ]
                },
            }
        )
        result = shop_stack(test_db, spec)
        # current has "high" friction → score 0.0
        assert result["current"].boundary_friction == 0.0
        # challenger has override "medium" → score 0.5
        assert result["challenger"].boundary_friction == 0.5

    def test_boundary_override_no_overrides_unchanged(self, test_db):
        """Spec without stack_boundary_overrides behaves as before."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "2",
                "components": {},
                "data_flow": {
                    "stages": [{"name": "a"}, {"name": "b"}],
                    "boundaries": [{"between": ["a", "b"], "friction": "low"}],
                },
                "candidate_stacks": {
                    "stack-x": {"framework": "PyTorch"},
                },
            }
        )
        result = shop_stack(test_db, spec)
        assert result["stack-x"].boundary_friction == 1.0  # low → 1.0


# ── C6: Backward compatibility ────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Verify existing shop() still works unchanged with v1 specs."""

    def test_shop_v1_spec_still_works(self, test_db):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "tracking": {"require": {"categories": ["experiment_tracking"]}},
                },
            }
        )
        from landscape.analysis.shop import shop as original_shop

        reports = original_shop(test_db, spec, top_n=5)
        assert "tracking" in reports
        assert isinstance(reports["tracking"], MatchReport)
        assert len(reports["tracking"].scored_tools) > 0

    def test_shop_coherence_uses_stack_pins(self, test_db):
        """Legacy shop() still passes stack_pins to coherence scorer."""
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "stack_pins": ["PyTorch"],
                "components": {
                    "tracking": {"require": {"categories": ["experiment_tracking"]}},
                },
            }
        )
        from landscape.analysis.shop import shop as original_shop

        reports = original_shop(test_db, spec, top_n=5)
        mlflow_hits = [s for s in reports["tracking"].scored_tools if s.tool_name == "MLflow"]
        assert mlflow_hits, "MLflow should appear as a candidate"
        assert mlflow_hits[0].coherence_score > 0, "MLflow should have coherence with PyTorch"

    def test_shop_print_still_works(self, test_db, capsys):
        spec = ProjectSpec.model_validate(
            {
                "spec_version": "1",
                "components": {
                    "tracking": {"require": {"categories": ["experiment_tracking"]}},
                },
            }
        )
        from landscape.analysis.shop import shop as original_shop

        reports = original_shop(test_db, spec, top_n=3)
        print_shop_report(reports)
        output = capsys.readouterr().out
        assert "tracking" in output
