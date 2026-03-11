"""Tests for the spec-driven shopping system models."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from landscape.models.spec import (
    SPEC_VERSION,
    WEIGHT_MULTIPLIERS,
    ComponentSpec,
    HardConstraints,
    ProjectSpec,
    WeightedPreference,
    parse_constraint_values,
)

# ── WeightedPreference ───────────────────────────────────────────────────────


class TestWeightedPreference:
    def test_from_shorthand_bare_value_gets_weight_3(self):
        wp = WeightedPreference.from_shorthand("growing")
        assert wp.value == "growing"
        assert wp.weight == 3

    def test_from_shorthand_bare_bool(self):
        wp = WeightedPreference.from_shorthand(True)
        assert wp.value is True
        assert wp.weight == 3

    def test_from_shorthand_explicit_dict(self):
        wp = WeightedPreference.from_shorthand({"value": "growing", "weight": 5})
        assert wp.value == "growing"
        assert wp.weight == 5

    def test_from_shorthand_explicit_dict_default_weight(self):
        wp = WeightedPreference.from_shorthand({"value": "native"})
        assert wp.value == "native"
        assert wp.weight == 3

    def test_multiplier_all_weights(self):
        for w in range(6):
            wp = WeightedPreference(value="x", weight=w)
            assert wp.multiplier == WEIGHT_MULTIPLIERS[w]

    def test_multiplier_weight_0(self):
        assert WeightedPreference(value="x", weight=0).multiplier == 0.0

    def test_multiplier_weight_5(self):
        assert WeightedPreference(value="x", weight=5).multiplier == 2.5

    def test_weight_boundary_0_valid(self):
        wp = WeightedPreference(value="x", weight=0)
        assert wp.weight == 0

    def test_weight_boundary_5_valid(self):
        wp = WeightedPreference(value="x", weight=5)
        assert wp.weight == 5

    def test_weight_6_invalid(self):
        with pytest.raises(Exception):  # ValidationError
            WeightedPreference(value="x", weight=6)

    def test_weight_negative_invalid(self):
        with pytest.raises(Exception):
            WeightedPreference(value="x", weight=-1)


# ── parse_constraint_values ──────────────────────────────────────────────────


class TestParseConstraintValues:
    def test_mixed_include_exclude(self):
        inc, exc = parse_constraint_values(["native", "!cloud_only"])
        assert inc == ["native"]
        assert exc == ["cloud_only"]

    def test_all_includes(self):
        inc, exc = parse_constraint_values(["native", "adaptable"])
        assert inc == ["native", "adaptable"]
        assert exc == []

    def test_all_excludes(self):
        inc, exc = parse_constraint_values(["!cloud_only", "!adaptable"])
        assert inc == []
        assert exc == ["cloud_only", "adaptable"]

    def test_empty_list(self):
        inc, exc = parse_constraint_values([])
        assert inc == []
        assert exc == []


# ── HardConstraints ──────────────────────────────────────────────────────────


class TestHardConstraints:
    def test_single_string_coercion_to_list(self):
        hc = HardConstraints(maturity="production")
        assert hc.maturity == ["production"]

    def test_single_string_coercion_multiple_fields(self):
        hc = HardConstraints(
            maturity="production",
            hpc_compatible="native",
            lock_in_risk="low",
        )
        assert hc.maturity == ["production"]
        assert hc.hpc_compatible == ["native"]
        assert hc.lock_in_risk == ["low"]

    def test_list_passed_through(self):
        hc = HardConstraints(maturity=["production", "growth"])
        assert hc.maturity == ["production", "growth"]

    def test_validate_enum_values_catches_invalid(self):
        hc = HardConstraints(maturity=["bogus_value"])
        errors = hc.validate_enum_values()
        assert len(errors) == 1
        assert "bogus_value" in errors[0]
        assert "require.maturity" in errors[0]

    def test_validate_enum_values_passes_valid(self):
        hc = HardConstraints(
            maturity=["production", "growth"],
            hpc_compatible=["native"],
            lock_in_risk=["low", "medium"],
        )
        errors = hc.validate_enum_values()
        assert errors == []

    def test_validate_enum_values_negation_passes(self):
        """Values with ! prefix should pass validation (prefix stripped before check)."""
        hc = HardConstraints(hpc_compatible=["!cloud_only"])
        errors = hc.validate_enum_values()
        assert errors == []

    def test_validate_enum_values_negation_invalid(self):
        hc = HardConstraints(hpc_compatible=["!nonexistent"])
        errors = hc.validate_enum_values()
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_get_known_fields_returns_non_none(self):
        hc = HardConstraints(
            python_native=True,
            maturity=["production"],
        )
        known = hc.get_known_fields()
        assert "python_native" in known
        assert "maturity" in known
        assert "offline_capable" not in known

    def test_get_known_fields_empty_when_all_none(self):
        hc = HardConstraints()
        assert hc.get_known_fields() == {}

    def test_get_extra_fields_returns_unknown(self):
        hc = HardConstraints(future_field="something")
        extras = hc.get_extra_fields()
        assert "future_field" in extras
        assert extras["future_field"] == "something"

    def test_get_extra_fields_empty_when_none(self):
        hc = HardConstraints(python_native=True)
        assert hc.get_extra_fields() == {}


# ── ComponentSpec ────────────────────────────────────────────────────────────


class TestComponentSpec:
    def test_preferences_parsed_from_shorthand(self):
        comp = ComponentSpec(
            prefer={
                "community_momentum": "growing",
                "python_native": True,
            }
        )
        prefs = comp.get_preferences()
        assert prefs["community_momentum"].value == "growing"
        assert prefs["community_momentum"].weight == 3
        assert prefs["python_native"].value is True

    def test_preferences_parsed_from_explicit_dicts(self):
        comp = ComponentSpec(
            prefer={
                "community_momentum": {"value": "growing", "weight": 5},
                "python_native": {"value": True, "weight": 1},
            }
        )
        prefs = comp.get_preferences()
        assert prefs["community_momentum"].weight == 5
        assert prefs["python_native"].weight == 1

    def test_validate_fields_catches_unknown_prefer(self):
        comp = ComponentSpec(prefer={"totally_fake_field": "yes"})
        errors = comp.validate_fields()
        assert any("totally_fake_field" in e for e in errors)
        assert any("not a known matchable field" in e for e in errors)

    def test_validate_fields_catches_bad_enum(self):
        comp = ComponentSpec(
            require=HardConstraints(maturity=["nonexistent"]),
        )
        errors = comp.validate_fields()
        assert any("nonexistent" in e for e in errors)

    def test_validate_fields_passes_valid(self):
        comp = ComponentSpec(
            require=HardConstraints(maturity=["production"], python_native=True),
            prefer={"community_momentum": "growing"},
        )
        errors = comp.validate_fields()
        assert errors == []

    def test_validate_fields_catches_unknown_extra_require(self):
        comp = ComponentSpec(
            require=HardConstraints(made_up_require="stuff"),
        )
        errors = comp.validate_fields()
        assert any("made_up_require" in e for e in errors)


# ── ProjectSpec ──────────────────────────────────────────────────────────────


class TestProjectSpec:
    def test_full_spec_from_dict(self):
        data = {
            "spec_version": SPEC_VERSION,
            "extends": ["base-hpc"],
            "project": {"name": "KD-GAT", "repo": "~/KD-GAT"},
            "environment": {
                "primary": "hpc",
                "secondary": ["local"],
                "gpu_required": True,
                "internet_on_compute": False,
                "shared_filesystem": "lustre",
            },
            "stack_pins": ["pytorch", "duckdb"],
            "components": {
                "experiment_tracking": {
                    "description": "Track ML experiments",
                    "current_tool": "mlflow",
                    "require": {
                        "python_native": True,
                        "maturity": ["production", "growth"],
                    },
                    "prefer": {
                        "community_momentum": "growing",
                        "lock_in_risk": {"value": "low", "weight": 5},
                    },
                    "notes": ["Must support SLURM"],
                    "triggers": ["if MLflow breaks on new PyTorch"],
                },
            },
            "weights": {"coherence": 1.5},
        }
        spec = ProjectSpec.model_validate(data)
        assert spec.spec_version == SPEC_VERSION
        assert spec.extends == ["base-hpc"]
        assert spec.project["name"] == "KD-GAT"
        assert spec.environment.primary == "hpc"
        assert spec.environment.gpu_required is True
        assert spec.environment.internet_on_compute is False
        assert spec.stack_pins == ["pytorch", "duckdb"]
        assert "experiment_tracking" in spec.components
        comp = spec.components["experiment_tracking"]
        assert comp.current_tool == "mlflow"
        assert comp.require.python_native is True
        assert comp.notes == ["Must support SLURM"]
        prefs = comp.get_preferences()
        assert prefs["lock_in_risk"].weight == 5

    def test_minimal_spec(self):
        data = {
            "spec_version": SPEC_VERSION,
            "components": {
                "data_storage": {
                    "require": {"open_source": True},
                }
            },
        }
        spec = ProjectSpec.model_validate(data)
        assert spec.spec_version == SPEC_VERSION
        assert len(spec.components) == 1
        assert spec.environment.internet_on_compute is True
        assert spec.stack_pins == []

    def test_inject_environment_offline_capable(self):
        """internet_on_compute=False should inject offline_capable=True."""
        data = {
            "spec_version": SPEC_VERSION,
            "environment": {"internet_on_compute": False},
            "components": {
                "a": {"require": {}},
                "b": {"require": {}},
            },
        }
        spec = ProjectSpec.model_validate(data)
        assert spec.components["a"].require.offline_capable is True
        assert spec.components["b"].require.offline_capable is True

    def test_inject_environment_does_not_override_explicit_false(self):
        """If offline_capable is explicitly False, internet_on_compute=False should not override."""
        data = {
            "spec_version": SPEC_VERSION,
            "environment": {"internet_on_compute": False},
            "components": {
                "a": {"require": {"offline_capable": False}},
            },
        }
        spec = ProjectSpec.model_validate(data)
        # The validator only injects when offline_capable is None, not when explicitly False
        assert spec.components["a"].require.offline_capable is False

    def test_inject_environment_no_effect_when_internet_true(self):
        data = {
            "spec_version": SPEC_VERSION,
            "environment": {"internet_on_compute": True},
            "components": {
                "a": {"require": {}},
            },
        }
        spec = ProjectSpec.model_validate(data)
        assert spec.components["a"].require.offline_capable is None

    def test_validate_spec_bad_version(self):
        spec = ProjectSpec(spec_version="99")
        errors = spec.validate_spec()
        assert any("spec_version" in e for e in errors)

    def test_validate_spec_correct_version(self):
        spec = ProjectSpec(spec_version=SPEC_VERSION)
        errors = spec.validate_spec()
        assert errors == []

    def test_validate_spec_propagates_component_errors(self):
        spec = ProjectSpec(
            components={
                "bad": ComponentSpec(
                    require=HardConstraints(maturity=["invalid_val"]),
                )
            }
        )
        errors = spec.validate_spec()
        assert any("components.bad" in e for e in errors)
        assert any("invalid_val" in e for e in errors)


# ── YAML round-trip ──────────────────────────────────────────────────────────


class TestYamlRoundTrip:
    def test_to_yaml_then_from_yaml_preserves_data(self):
        original = ProjectSpec(
            spec_version=SPEC_VERSION,
            project={"name": "test-project"},
            environment={"primary": "hpc", "gpu_required": True},
            stack_pins=["pytorch"],
            components={
                "tracker": ComponentSpec(
                    description="Track experiments",
                    current_tool="mlflow",
                    require=HardConstraints(
                        python_native=True,
                        maturity=["production"],
                    ),
                    prefer={
                        "community_momentum": {"value": "growing", "weight": 4},
                        "open_source": True,
                    },
                    notes=["Must be offline"],
                    triggers=["yearly review"],
                )
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.yaml"
            original.to_yaml(path)
            loaded = ProjectSpec.from_yaml(path)

        assert loaded.project["name"] == "test-project"
        assert loaded.stack_pins == ["pytorch"]
        assert loaded.components["tracker"].current_tool == "mlflow"
        assert loaded.components["tracker"].require.python_native is True
        assert loaded.components["tracker"].require.maturity == ["production"]
        prefs = loaded.components["tracker"].get_preferences()
        assert prefs["community_momentum"].value == "growing"
        assert prefs["community_momentum"].weight == 4
        assert prefs["open_source"].value is True
        assert loaded.components["tracker"].notes == ["Must be offline"]

    def test_from_yaml_empty_file_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.yaml"
            path.write_text("")
            with pytest.raises(ValueError, match="Empty YAML"):
                ProjectSpec.from_yaml(path)

    def test_shorthand_preferences_survive_yaml_roundtrip(self):
        """Weight-3 prefs should round-trip through YAML and retain correct weights.

        Note: to_yaml uses exclude_defaults=True, which strips weight=3 (the default).
        The shorthand conversion in to_yaml checks pref.get("weight") == 3, which
        won't match when weight is absent (excluded as default). This means weight-3
        prefs are stored as {"value": ...} dicts, not bare values. They still
        round-trip correctly because from_shorthand handles both forms.
        """
        original = ProjectSpec(
            components={
                "comp": ComponentSpec(
                    prefer={
                        "community_momentum": "growing",  # shorthand → weight 3
                        "lock_in_risk": {"value": "low", "weight": 5},  # explicit
                    }
                )
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.yaml"
            original.to_yaml(path)

            import yaml

            data = yaml.safe_load(raw := path.read_text())
            comp_prefs = data["components"]["comp"]["prefer"]
            # Weight-3 stored as {"value": "growing"} (weight excluded as default)
            assert comp_prefs["community_momentum"] == {"value": "growing"}
            # Weight-5 → dict preserved with weight
            assert isinstance(comp_prefs["lock_in_risk"], dict)
            assert comp_prefs["lock_in_risk"]["weight"] == 5

            # Round-trip restores correct weights via from_shorthand
            loaded = ProjectSpec.from_yaml(path)
            prefs = loaded.components["comp"].get_preferences()
            assert prefs["community_momentum"].weight == 3
            assert prefs["community_momentum"].value == "growing"
            assert prefs["lock_in_risk"].weight == 5
            assert prefs["lock_in_risk"].value == "low"

    def test_from_yaml_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.yaml"
            path.write_text(
                "spec_version: '1'\n"
                "components:\n"
                "  storage:\n"
                "    require:\n"
                "      open_source: true\n"
            )
            spec = ProjectSpec.from_yaml(path)
            assert spec.components["storage"].require.open_source is True
