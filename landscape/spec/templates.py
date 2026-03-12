"""Template loading, merging, and init command support.

Templates are plain YAML spec files in data/templates/. A spec can extend
one or more templates via the `extends` field. Merge rules:

1. environment: deep-merge, later values override
2. components: merged by name, later template's fields override earlier's.
   Within a component, require/prefer are deep-merged.
3. stack_pins: concatenated (union, deduplicated)
4. weights: later overrides earlier
5. User's explicit spec always wins over any template
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from landscape.models.spec import ProjectSpec

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "data" / "templates"


def list_templates() -> list[str]:
    """Return available template names (without .yaml extension)."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml"))


def load_template(name: str) -> dict:
    """Load a template by name. Returns raw dict (not parsed as ProjectSpec yet)."""
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Template '{name}' not found at {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"Empty template: {path}")
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge two dicts. Override values win for non-dict leaves."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_specs(base: dict, overlay: dict) -> dict:
    """Merge two spec dicts following the merge rules.

    Args:
        base: The template or earlier spec.
        overlay: The later template or user spec (wins on conflict).

    Returns:
        Merged spec dict.
    """
    result = copy.deepcopy(base)

    # environment: deep-merge
    if "environment" in overlay:
        result["environment"] = _deep_merge(result.get("environment", {}), overlay["environment"])

    # project: deep-merge
    if "project" in overlay:
        result["project"] = _deep_merge(result.get("project", {}), overlay["project"])

    # components: merge by name, deep-merge within each component
    if "components" in overlay:
        base_components = result.get("components", {})
        for comp_name, comp_data in overlay["components"].items():
            if comp_name in base_components:
                base_components[comp_name] = _deep_merge(base_components[comp_name], comp_data)
            else:
                base_components[comp_name] = copy.deepcopy(comp_data)
        result["components"] = base_components

    # stack_pins: union (deduplicated, preserving order)
    if "stack_pins" in overlay:
        existing = result.get("stack_pins", [])
        new = overlay["stack_pins"]
        seen = set(existing)
        merged = list(existing)
        for pin in new:
            if pin not in seen:
                merged.append(pin)
                seen.add(pin)
        result["stack_pins"] = merged

    # weights: later overrides
    if "weights" in overlay:
        result["weights"] = {**result.get("weights", {}), **overlay["weights"]}

    # ── v2 fields ────────────────────────────────────────────────────────────

    # data_flow: stages concatenated deduplicated by name (last wins); boundaries concatenated
    if "data_flow" in overlay:
        base_df = result.get("data_flow") or {}
        over_df = overlay["data_flow"] or {}

        # Merge stages: last definition of a stage name wins
        base_stages: list[dict] = base_df.get("stages", [])
        over_stages: list[dict] = over_df.get("stages", [])
        stages_by_name: dict[str, dict] = {s["name"]: s for s in base_stages}
        for s in over_stages:
            stages_by_name[s["name"]] = copy.deepcopy(s)
        merged_stages = list(stages_by_name.values())

        # Boundaries: concatenated (no dedup — may represent distinct edges)
        merged_boundaries = list(base_df.get("boundaries", [])) + list(
            copy.deepcopy(over_df.get("boundaries", []))
        )

        result["data_flow"] = {"stages": merged_stages, "boundaries": merged_boundaries}

    # time_horizon: planned_work concatenated; dicts merged (last wins)
    if "time_horizon" in overlay:
        base_th = result.get("time_horizon") or {}
        over_th = overlay["time_horizon"] or {}

        merged_pw = list(base_th.get("planned_work", [])) + list(
            copy.deepcopy(over_th.get("planned_work", []))
        )
        merged_ct = {**base_th.get("ceiling_timeline", {}), **over_th.get("ceiling_timeline", {})}
        merged_ev = {**base_th.get("evolution", {}), **over_th.get("evolution", {})}

        result["time_horizon"] = {
            "planned_work": merged_pw,
            "ceiling_timeline": merged_ct,
            "evolution": merged_ev,
        }

    # migration: one_time and ongoing_friction dict-merged (last wins)
    if "migration" in overlay:
        base_mg = result.get("migration") or {}
        over_mg = overlay["migration"] or {}
        result["migration"] = {
            "one_time": {**base_mg.get("one_time", {}), **over_mg.get("one_time", {})},
            "ongoing_friction": {
                **base_mg.get("ongoing_friction", {}),
                **over_mg.get("ongoing_friction", {}),
            },
        }

    # candidate_stacks: dict-merged (last wins per stack name)
    if "candidate_stacks" in overlay:
        result["candidate_stacks"] = {
            **result.get("candidate_stacks", {}),
            **copy.deepcopy(overlay["candidate_stacks"]),
        }

    # stack_boundary_overrides: dict-merged by stack name (last wins per stack)
    if "stack_boundary_overrides" in overlay:
        result["stack_boundary_overrides"] = {
            **result.get("stack_boundary_overrides", {}),
            **copy.deepcopy(overlay["stack_boundary_overrides"]),
        }

    # invariant_pins: union (like stack_pins)
    if "invariant_pins" in overlay:
        existing = result.get("invariant_pins", [])
        new = overlay["invariant_pins"]
        seen = set(existing)
        merged = list(existing)
        for pin in new:
            if pin not in seen:
                merged.append(pin)
                seen.add(pin)
        result["invariant_pins"] = merged

    # extends: don't propagate (already resolved)
    result.pop("extends", None)

    # spec_version: overlay wins
    if "spec_version" in overlay:
        result["spec_version"] = overlay["spec_version"]

    return result


def resolve_extends(spec_data: dict) -> dict:
    """Resolve the `extends` field by loading and merging templates.

    Templates are merged left-to-right, then the user spec is overlaid.
    """
    extends = spec_data.get("extends", [])
    if not extends:
        return spec_data

    # Start with first template
    result = load_template(extends[0])

    # Merge remaining templates left-to-right
    for template_name in extends[1:]:
        template = load_template(template_name)
        result = merge_specs(result, template)

    # User spec wins over all templates
    user_data = {k: v for k, v in spec_data.items() if k != "extends"}
    result = merge_specs(result, user_data)

    return result


def load_spec_with_templates(path: str | Path) -> ProjectSpec:
    """Load a spec file, resolving any template extends."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"Empty YAML file: {path}")

    resolved = resolve_extends(raw)
    return ProjectSpec.model_validate(resolved)


def init_spec(template_names: list[str], output_path: str | Path) -> ProjectSpec:
    """Create a new spec file from one or more templates.

    Args:
        template_names: List of template names to merge.
        output_path: Where to write the resulting spec YAML.

    Returns:
        The created ProjectSpec.
    """
    if not template_names:
        raise ValueError("At least one template name required")

    # Start with first template
    result = load_template(template_names[0])

    # Merge additional templates
    for name in template_names[1:]:
        template = load_template(name)
        result = merge_specs(result, template)

    # Remove extends (already resolved)
    result.pop("extends", None)

    # Parse to validate
    spec = ProjectSpec.model_validate(result)

    # Write to file
    spec.to_yaml(output_path)

    return spec
