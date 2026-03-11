#!/usr/bin/env python3
"""Validate all seed catalog JSON files for schema correctness and cross-catalog duplicates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "seed"

REQUIRED_KEYS = {
    "name",
    "url",
    "open_source",
    "license",
    "summary",
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
    "python_native",
    "offline_capable",
    "saas_available",
    "self_hosted_viable",
    "composite_tool",
    "categories",
    "deployment_model",
    "language_ecosystem",
    "integration_targets",
    "pipeline_stage",
    "scale_profile",
    "used_by",
}

VALID_ENUMS = {
    "maturity": {"experimental", "early", "growth", "production", "archived", ""},
    "governance": {
        "community",
        "company_backed",
        "foundation",
        "apache_foundation",
        "cncf",
        "linux_foundation",
        "",
    },
    "hpc_compatible": {"native", "adaptable", "cloud_only", "unknown", ""},
    "collaboration_model": {"single_user", "shared_server", "multi_tenant", "unknown", ""},
    "migration_cost": {"low", "medium", "high", "unknown", ""},
    "lock_in_risk": {"low", "medium", "high", "unknown", ""},
    "community_momentum": {"growing", "stable", "declining", "unknown", ""},
    "documentation_quality": {"excellent", "adequate", "unknown", ""},
    "resource_overhead": {"minimal", "moderate", "heavy", "unknown", ""},
    "interoperability": {"extensive", "high", "medium", "low", "unknown", ""},
    "capability_ceiling": {"extensive", "high", "medium", "low", "unknown", ""},
    "migration_likelihood": {"low", "medium", "high", "unknown", ""},
}


def validate() -> int:
    catalogs = sorted(SEED_DIR.glob("*_catalog*.json"))
    if not catalogs:
        print("No catalog files found!")
        return 1

    all_names: dict[str, str] = {}  # name -> catalog file
    errors = 0
    total_tools = 0

    for catalog_path in catalogs:
        cat_name = catalog_path.name
        try:
            tools = json.loads(catalog_path.read_text())
        except json.JSONDecodeError as e:
            print(f"INVALID JSON in {cat_name}: {e}")
            errors += 1
            continue

        if not isinstance(tools, list):
            print(f"{cat_name}: Expected list, got {type(tools).__name__}")
            errors += 1
            continue

        print(f"\n{cat_name}: {len(tools)} tools")
        total_tools += len(tools)

        for i, t in enumerate(tools):
            name = t.get("name", f"<unnamed-{i}>")

            # Check for duplicate names across catalogs
            if name in all_names:
                print(f"  DUPLICATE: '{name}' (also in {all_names[name]})")
                errors += 1
            all_names[name] = cat_name

            # Check required keys
            missing = REQUIRED_KEYS - set(t.keys())
            if missing:
                print(f"  {name}: missing keys: {missing}")
                errors += 1

            extra = set(t.keys()) - REQUIRED_KEYS
            if extra:
                print(f"  {name}: extra keys: {extra}")

            # Check enum values
            for field, valid_values in VALID_ENUMS.items():
                val = t.get(field, "")
                if val and val not in valid_values:
                    print(f"  {name}: invalid {field}='{val}' (valid: {valid_values})")
                    errors += 1

            # Check array fields are actually lists
            for arr_field in [
                "categories",
                "deployment_model",
                "language_ecosystem",
                "integration_targets",
                "pipeline_stage",
                "used_by",
            ]:
                val = t.get(arr_field)
                if val is not None and not isinstance(val, list):
                    print(f"  {name}: {arr_field} should be list, got {type(val).__name__}")
                    errors += 1

            # Check bool fields
            for bool_field in [
                "open_source",
                "python_native",
                "offline_capable",
                "saas_available",
                "self_hosted_viable",
                "composite_tool",
            ]:
                val = t.get(bool_field)
                if val is not None and not isinstance(val, bool):
                    print(f"  {name}: {bool_field} should be bool, got {type(val).__name__}")
                    errors += 1

    print(f"\n{'=' * 50}")
    print(f"Total: {total_tools} tools across {len(catalogs)} catalogs")
    print(f"Unique names: {len(all_names)}")
    print(f"Errors: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(validate())
