"""Pydantic v2 models for the spec-driven shopping system.

A spec defines what a project needs from its tool stack:
- Hard constraints (require) eliminate tools that don't match
- Weighted preferences (prefer) rank surviving tools
- Notes capture requirements that can't be auto-checked
- Triggers define when to re-evaluate a component

Specs can extend templates and pin stack tools for coherence scoring.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ── Constants ────────────────────────────────────────────────────────────────

SPEC_VERSION = "1"

# Valid enum values per field (must match DuckDB schema enums)
VALID_ENUMS: dict[str, list[str]] = {
    "maturity": ["archived", "experimental", "early", "growth", "production"],
    "governance": [
        "community",
        "company_backed",
        "foundation",
        "apache_foundation",
        "cncf",
        "linux_foundation",
    ],
    "hpc_compatible": ["cloud_only", "adaptable", "native"],
    "collaboration_model": ["single_user", "shared_server", "multi_tenant"],
    "migration_cost": ["low", "medium", "high"],
    "lock_in_risk": ["low", "medium", "high"],
    "community_momentum": ["declining", "stable", "growing"],
    "documentation_quality": ["poor", "adequate", "excellent"],
    "resource_overhead": ["minimal", "moderate", "heavy"],
    "interoperability": ["low", "medium", "high", "extensive"],
    "capability_ceiling": ["low", "medium", "high", "extensive"],
    "migration_likelihood": ["low", "medium", "high"],
}

# Boolean fields on the tools table
BOOLEAN_FIELDS: set[str] = {
    "python_native",
    "offline_capable",
    "open_source",
    "saas_available",
    "self_hosted_viable",
    "composite_tool",
}

# Array fields on the tools table
ARRAY_FIELDS: set[str] = {
    "categories",
    "deployment_model",
    "language_ecosystem",
    "integration_targets",
    "pipeline_stages",
    "scale_profiles",
    "used_by",
}

# All matchable field names (Tier 1 — direct column match)
MATCHABLE_FIELDS: set[str] = BOOLEAN_FIELDS | set(VALID_ENUMS) | ARRAY_FIELDS

# Metric threshold fields (Tier 2 — requires join to tool_metrics)
METRIC_FIELDS: dict[str, str] = {
    "min_stars": "github_stars",
    "min_downloads": "pypi_downloads_monthly",
    "max_days_since_release": "days_since_last_release",
    "min_openssf_score": "openssf_score",
}

# Weight multiplier table (weight 0-5 → scoring multiplier)
WEIGHT_MULTIPLIERS: dict[int, float] = {
    0: 0.0,
    1: 0.2,
    2: 0.6,
    3: 1.0,
    4: 1.5,
    5: 2.5,
}

# Ordinal preference scales: field → ordered values (lowest → highest)
ORDINAL_HIGHER_BETTER: dict[str, list[str]] = {
    "capability_ceiling": ["low", "medium", "high", "extensive"],
    "community_momentum": ["declining", "stable", "growing"],
    "documentation_quality": ["poor", "adequate", "excellent"],
    "interoperability": ["low", "medium", "high", "extensive"],
    "maturity": ["archived", "experimental", "early", "growth", "production"],
}

ORDINAL_LOWER_BETTER: dict[str, list[str]] = {
    "lock_in_risk": ["low", "medium", "high"],
    "migration_cost": ["low", "medium", "high"],
    "resource_overhead": ["minimal", "moderate", "heavy"],
    "migration_likelihood": ["low", "medium", "high"],
}

_NEGATION_RE = re.compile(r"^!(.+)$")


# ── Helper: parse negation ───────────────────────────────────────────────────


def parse_constraint_values(values: list[str]) -> tuple[list[str], list[str]]:
    """Split a list of constraint values into include and exclude lists.

    Values prefixed with '!' are exclusions.
    Returns (include_values, exclude_values) with '!' stripped.
    """
    include: list[str] = []
    exclude: list[str] = []
    for v in values:
        m = _NEGATION_RE.match(v)
        if m:
            exclude.append(m.group(1))
        else:
            include.append(v)
    return include, exclude


# ── Models ───────────────────────────────────────────────────────────────────


class WeightedPreference(BaseModel):
    """A soft preference with a weight (0-5).

    Can be created from shorthand (bare value → weight 3) or explicit dict.
    """

    value: Any
    weight: Annotated[int, Field(ge=0, le=5)] = 3

    @classmethod
    def from_shorthand(cls, raw: Any) -> WeightedPreference:
        """Parse shorthand or explicit preference.

        Shorthand: bare value → weight 3
        Explicit: {value: ..., weight: N}
        """
        if isinstance(raw, dict) and "value" in raw:
            return cls(value=raw["value"], weight=raw.get("weight", 3))
        # Shorthand: bare value
        return cls(value=raw, weight=3)

    @property
    def multiplier(self) -> float:
        """Get the scoring multiplier for this weight."""
        return WEIGHT_MULTIPLIERS[self.weight]


class HardConstraints(BaseModel, extra="allow"):
    """Hard constraints that eliminate tools.

    Known fields are validated against the schema. Unknown fields are allowed
    (extra="allow") and reported as unmatched during validation — they'll
    auto-activate if a matching column is later added to the DB.
    """

    # Booleans
    python_native: bool | None = None
    offline_capable: bool | None = None
    open_source: bool | None = None
    saas_available: bool | None = None
    self_hosted_viable: bool | None = None
    composite_tool: bool | None = None

    # Enums (list of allowed values, may include ! negation)
    maturity: list[str] | None = None
    governance: list[str] | None = None
    hpc_compatible: list[str] | None = None
    collaboration_model: list[str] | None = None
    migration_cost: list[str] | None = None
    lock_in_risk: list[str] | None = None
    community_momentum: list[str] | None = None
    documentation_quality: list[str] | None = None
    resource_overhead: list[str] | None = None
    interoperability: list[str] | None = None
    capability_ceiling: list[str] | None = None
    migration_likelihood: list[str] | None = None

    # Arrays (tool must contain at least one, may include ! negation)
    categories: list[str] | None = None
    deployment_model: list[str] | None = None
    language_ecosystem: list[str] | None = None
    integration_targets: list[str] | None = None
    pipeline_stages: list[str] | None = None
    scale_profiles: list[str] | None = None
    used_by: list[str] | None = None

    # Metric thresholds
    min_stars: int | None = None
    min_downloads: int | None = None
    max_days_since_release: int | None = None
    min_openssf_score: float | None = None

    @field_validator(
        "maturity",
        "governance",
        "hpc_compatible",
        "collaboration_model",
        "migration_cost",
        "lock_in_risk",
        "community_momentum",
        "documentation_quality",
        "resource_overhead",
        "interoperability",
        "capability_ceiling",
        "migration_likelihood",
        mode="before",
    )
    @classmethod
    def coerce_to_list(cls, v: Any) -> list[str] | None:
        """Accept single string as a one-element list."""
        if v is None:
            return None
        if isinstance(v, str):
            return [v]
        return v

    def get_known_fields(self) -> dict[str, Any]:
        """Return only the explicitly set known fields (non-None)."""
        result = {}
        for name in type(self).model_fields:
            val = getattr(self, name)
            if val is not None:
                result[name] = val
        return result

    def get_extra_fields(self) -> dict[str, Any]:
        """Return fields not in the schema (unknown/future fields)."""
        if self.__pydantic_extra__:
            return dict(self.__pydantic_extra__)
        return {}

    def validate_enum_values(self) -> list[str]:
        """Check that enum constraint values are valid. Returns list of errors."""
        errors = []
        for field_name, valid_values in VALID_ENUMS.items():
            values = getattr(self, field_name, None)
            if values is None:
                continue
            for v in values:
                raw = v.lstrip("!")
                if raw not in valid_values:
                    errors.append(
                        f"require.{field_name}: '{raw}' is not a valid value "
                        f"(expected one of {valid_values})"
                    )
        return errors


class ComponentSpec(BaseModel):
    """Specification for a single project component (e.g., experiment_tracking)."""

    description: str = ""
    current_tool: str | None = None
    require: HardConstraints = Field(default_factory=HardConstraints)
    prefer: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def parse_preferences(self) -> ComponentSpec:
        """Convert raw prefer dict values to WeightedPreference objects."""
        parsed: dict[str, WeightedPreference] = {}
        for key, raw in self.prefer.items():
            if isinstance(raw, WeightedPreference):
                parsed[key] = raw
            else:
                parsed[key] = WeightedPreference.from_shorthand(raw)
        self.prefer = parsed  # type: ignore[assignment]
        return self

    def get_preferences(self) -> dict[str, WeightedPreference]:
        """Return preferences as WeightedPreference objects."""
        return self.prefer  # type: ignore[return-value]

    def validate_fields(self) -> list[str]:
        """Validate that require/prefer fields reference known tool columns."""
        errors = self.require.validate_enum_values()

        # Check prefer field names
        for field_name in self.prefer:
            if field_name not in MATCHABLE_FIELDS and field_name not in METRIC_FIELDS:
                errors.append(
                    f"prefer.{field_name}: not a known matchable field "
                    f"(will be ignored during scoring)"
                )

        # Check extra require fields
        for field_name in self.require.get_extra_fields():
            if field_name not in MATCHABLE_FIELDS and field_name not in METRIC_FIELDS:
                errors.append(
                    f"require.{field_name}: not a known matchable field "
                    f"(will be ignored during filtering)"
                )

        return errors


class EnvironmentSpec(BaseModel):
    """Shared environment constraints applied to all components."""

    primary: str | None = None  # hpc | cloud | local | edge
    secondary: list[str] = Field(default_factory=list)
    gpu_required: bool = False
    internet_on_compute: bool = True
    shared_filesystem: str | None = None


class ProjectSpec(BaseModel):
    """Top-level spec: project metadata + environment + components."""

    spec_version: str = SPEC_VERSION
    extends: list[str] = Field(default_factory=list)

    # Project metadata
    project: dict[str, Any] = Field(default_factory=dict)

    # Environment
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)

    # Stack pins (tools already committed to — boosts coherence)
    stack_pins: list[str] = Field(default_factory=list)

    # Components (the shopping list)
    components: dict[str, ComponentSpec] = Field(default_factory=dict)

    # Optional weight overrides
    weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def inject_environment_constraints(self) -> ProjectSpec:
        """Auto-inject environment-derived constraints into components.

        If internet_on_compute is False, inject offline_capable=true into every
        component's require (unless already specified).
        """
        if not self.environment.internet_on_compute:
            for comp in self.components.values():
                if comp.require.offline_capable is None:
                    comp.require.offline_capable = True
        return self

    def validate_spec(self) -> list[str]:
        """Run all validation checks. Returns list of error/warning messages."""
        errors: list[str] = []

        # Version check
        if self.spec_version != SPEC_VERSION:
            errors.append(f"spec_version '{self.spec_version}' != current '{SPEC_VERSION}'")

        # Component validation
        for comp_name, comp in self.components.items():
            comp_errors = comp.validate_fields()
            for e in comp_errors:
                errors.append(f"components.{comp_name}.{e}")

        return errors

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProjectSpec:
        """Load a spec from a YAML file."""
        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            raise ValueError(f"Empty YAML file: {path}")
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        """Write spec to a YAML file."""
        path = Path(path)
        data = self.model_dump(
            exclude_none=True,
            exclude_defaults=True,
        )
        # Convert WeightedPreference objects back to shorthand where possible
        for comp_name, comp_data in data.get("components", {}).items():
            if "prefer" in comp_data:
                for key, pref in comp_data["prefer"].items():
                    if isinstance(pref, dict) and pref.get("weight") == 3:
                        comp_data["prefer"][key] = pref["value"]
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
