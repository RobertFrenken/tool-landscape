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
