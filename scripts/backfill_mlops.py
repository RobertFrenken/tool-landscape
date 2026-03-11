#!/usr/bin/env python3
"""Backfill missing summaries and enum fields for mlops catalog tools.

Usage: python scripts/backfill_mlops.py --start 0 --end 98 [--dry-run]

Generates reasonable enum values and summaries based on each tool's
existing fields (name, categories, maturity, governance, URL, etc.).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "seed" / "mlops_tools_catalog.json"

# Fields to backfill when missing/unknown
ENUM_FIELDS = [
    "community_momentum",
    "capability_ceiling",
    "migration_cost",
    "lock_in_risk",
    "documentation_quality",
    "interoperability",
    "migration_likelihood",
]

# Heuristic rules for enum assignment based on existing tool properties
# These provide reasonable defaults — not perfect, but better than "unknown"


def _infer_momentum(t: dict) -> str:
    """Infer community momentum from maturity and governance."""
    maturity = t.get("maturity", "")
    gov = t.get("governance", "")
    cats = t.get("categories", [])

    # Archived/experimental tools are typically declining or stable
    if maturity == "archived":
        return "declining"
    if maturity == "experimental":
        return "stable"

    # Company-backed production tools tend to be stable or growing
    if maturity == "production" and gov == "company_backed":
        return "stable"
    if maturity == "production" and gov in ("community", "foundation", "cncf", "linux_foundation"):
        return "growing"
    if maturity == "growth":
        return "growing"

    return "stable"


def _infer_ceiling(t: dict) -> str:
    """Infer capability ceiling from categories and maturity."""
    cats = t.get("categories", [])
    maturity = t.get("maturity", "")

    # Platform/orchestrator tools typically have high/extensive ceiling
    high_ceiling_cats = {
        "ml_platform",
        "orchestrator",
        "distributed_computing",
        "database",
        "data_warehouse",
        "cloud_platform",
    }
    if any(c in high_ceiling_cats for c in cats):
        return "extensive" if maturity == "production" else "high"

    # Monitoring, tracking, serving tools are typically high
    mid_ceiling_cats = {
        "experiment_tracking",
        "monitoring",
        "model_serving",
        "feature_store",
        "data_versioning",
        "etl",
    }
    if any(c in mid_ceiling_cats for c in cats):
        return "high"

    # Niche/single-purpose tools tend to be medium
    niche_cats = {
        "explainability",
        "data_labeling",
        "data_quality",
        "privacy",
        "hyperparameter_tuning",
    }
    if any(c in niche_cats for c in cats):
        return "medium"

    # Default based on maturity
    if maturity == "production":
        return "high"
    if maturity in ("growth", "early"):
        return "medium"
    return "low"


def _infer_migration_cost(t: dict) -> str:
    """Infer migration cost."""
    cats = t.get("categories", [])

    # Platforms and frameworks have high migration cost
    if any(c in ("ml_platform", "ml_framework", "database", "orchestrator") for c in cats):
        return "high"

    # Libraries and tools are typically medium
    if any(c in ("experiment_tracking", "feature_store", "model_serving") for c in cats):
        return "medium"

    return "low"


def _infer_lock_in_risk(t: dict) -> str:
    """Infer lock-in risk."""
    open_source = t.get("open_source", False)
    gov = t.get("governance", "")
    cats = t.get("categories", [])

    if not open_source:
        return "high"
    if gov == "company_backed" and any(c in ("ml_platform", "cloud_platform") for c in cats):
        return "medium"
    if open_source and gov in ("community", "foundation", "cncf", "apache_foundation"):
        return "low"

    return "medium"


def _infer_doc_quality(t: dict) -> str:
    """Infer documentation quality."""
    maturity = t.get("maturity", "")
    gov = t.get("governance", "")

    if maturity == "production" and gov in ("company_backed", "cncf", "apache_foundation"):
        return "excellent"
    if maturity == "production":
        return "adequate"
    return "adequate"


def _infer_interop(t: dict) -> str:
    """Infer interoperability."""
    targets = t.get("integration_targets", [])
    cats = t.get("categories", [])

    if len(targets) >= 5:
        return "extensive"
    if len(targets) >= 3:
        return "high"
    if any(c in ("ml_platform", "orchestrator", "etl") for c in cats):
        return "high"
    return "medium"


def _infer_migration_likelihood(t: dict) -> str:
    """Infer migration likelihood (how likely users are to switch away)."""
    maturity = t.get("maturity", "")
    gov = t.get("governance", "")

    if maturity in ("archived", "experimental"):
        return "high"
    if maturity == "production" and gov in ("community", "cncf"):
        return "low"
    if maturity == "production":
        return "low"
    return "medium"


CATEGORY_LABELS = {
    "automl": "automated machine learning framework",
    "ml_platform": "machine learning platform",
    "ml_framework": "machine learning framework",
    "experiment_tracking": "experiment tracking tool",
    "model_serving": "model serving and deployment tool",
    "model_optimization": "model optimization toolkit",
    "feature_store": "feature store",
    "feature_engineering": "feature engineering tool",
    "data_versioning": "data versioning system",
    "data_quality": "data quality and validation tool",
    "data_labeling": "data labeling platform",
    "data_catalog": "data catalog and discovery tool",
    "data_management": "data management tool",
    "monitoring": "monitoring and observability tool",
    "orchestrator": "workflow orchestration platform",
    "distributed_computing": "distributed computing framework",
    "etl": "ETL and data integration tool",
    "database": "database system",
    "data_warehouse": "data warehouse",
    "cloud_platform": "cloud platform",
    "container_runtime": "container runtime",
    "container_registry": "container registry",
    "ci_cd": "CI/CD platform",
    "infrastructure_as_code": "infrastructure-as-code tool",
    "config_management": "configuration management tool",
    "service_mesh": "service mesh",
    "secret_management": "secret management tool",
    "visualization": "data visualization tool",
    "explainability": "model explainability tool",
    "privacy": "privacy-preserving ML tool",
    "hyperparameter_tuning": "hyperparameter optimization tool",
    "notebook": "notebook environment",
    "linter": "code quality and linting tool",
    "testing": "testing framework",
    "package_manager": "package manager",
    "fine_tuning": "model fine-tuning tool",
    "message_queue": "message queue",
    "serialization": "data serialization format",
    "metadata_store": "metadata store",
    "scheduling": "job scheduling tool",
}


def _generate_summary(t: dict) -> str:
    """Generate a short summary from tool name, categories, and context."""
    name = t["name"]
    cats = t.get("categories", [])
    langs = t.get("language_ecosystem", [])
    stages = t.get("pipeline_stage", [])
    open_source = t.get("open_source", False)

    # Get human-readable category label
    primary_cat = cats[0] if cats else "tool"
    label = CATEGORY_LABELS.get(primary_cat, primary_cat.replace("_", " ") + " tool")

    # Build language context
    lang_part = ""
    if langs:
        lang_part = f" for {'/'.join(langs[:2])}"

    # Build stage context
    stage_part = ""
    if stages:
        readable_stages = [s.replace("_", " ") for s in stages[:2]]
        stage_part = f", supporting {' and '.join(readable_stages)}"

    # Open source context
    oss_part = "Open-source " if open_source else ""

    summary = f"{oss_part}{label}{lang_part}{stage_part}."
    if len(summary) > 200:
        summary = summary[:197] + "..."

    return summary


def backfill(start: int, end: int, dry_run: bool = False) -> int:
    """Backfill tools in the given index range."""
    tools = json.loads(CATALOG_PATH.read_text())

    # Find tools missing summary
    missing_indices = [i for i, t in enumerate(tools) if not t.get("summary")]
    batch = missing_indices[start:end]

    count = 0
    for idx in batch:
        t = tools[idx]

        # Generate summary
        if not t.get("summary"):
            t["summary"] = _generate_summary(t)

        # Fill enum fields
        infer_funcs = {
            "community_momentum": _infer_momentum,
            "capability_ceiling": _infer_ceiling,
            "migration_cost": _infer_migration_cost,
            "lock_in_risk": _infer_lock_in_risk,
            "documentation_quality": _infer_doc_quality,
            "interoperability": _infer_interop,
            "migration_likelihood": _infer_migration_likelihood,
        }

        for field, func in infer_funcs.items():
            if not t.get(field) or t.get(field) == "unknown":
                t[field] = func(t)

        count += 1

    if not dry_run:
        CATALOG_PATH.write_text(json.dumps(tools, indent=2) + "\n")
        print(f"Updated {count} tools (indices {start}-{end}) in {CATALOG_PATH.name}")
    else:
        print(f"[DRY RUN] Would update {count} tools (indices {start}-{end})")
        # Show sample
        if batch:
            sample = tools[batch[0]]
            print(f"  Sample: {sample['name']}")
            print(f"    summary: {sample.get('summary', '')}")
            for f in ENUM_FIELDS:
                print(f"    {f}: {sample.get(f, '')}")

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill mlops catalog fields")
    parser.add_argument("--start", type=int, default=0, help="Start index in missing-tools list")
    parser.add_argument("--end", type=int, default=394, help="End index (exclusive)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    count = backfill(args.start, args.end, dry_run=args.dry_run)
    print(f"Done: {count} tools processed")


if __name__ == "__main__":
    main()
